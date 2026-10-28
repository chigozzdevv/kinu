import json
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from connectors.aws import AwsApi
from connectors.aws.client import AwsError
from core.errors import ResourceConflictError, ResourceNotFoundError

from mail.models import Mailbox


class Store:
    def __init__(self, aws: AwsApi, region: str, table: str) -> None:
        self.aws, self.region, self.table = aws, region, table

    async def call(self, operation: str, **values: Any) -> dict[str, Any]:
        try:
            return await self.aws.call(
                "dynamodb", operation, self.region, TableName=self.table, **values
            )
        except AwsError as error:
            if error.code == "ConditionalCheckFailedException":
                raise ResourceConflictError(
                    "Gmail connection changed; refresh and try again"
                ) from None
            raise

    async def get(self, key: str) -> dict[str, Any] | None:
        response = await self.call("get_item", Key=encode({"pk": key}), ConsistentRead=True)
        return decode(response["Item"]) if "Item" in response else None

    async def pending(self, key: str, value: dict[str, Any]) -> None:
        await self.call(
            "put_item",
            Item=encode(value | {"pk": "pending:" + key}),
            ConditionExpression="attribute_not_exists(pk)",
        )

    async def consume(
        self, state: str, session: str, actor: str, org: str, now: int
    ) -> dict[str, Any]:
        result = await self.call(
            "delete_item",
            Key=encode({"pk": "pending:" + state}),
            ConditionExpression=(
                "#binding=:binding AND actor=:actor AND organisation_id=:org AND #expires>:now"
            ),
            ExpressionAttributeNames={"#binding": "binding", "#expires": "expires"},
            ExpressionAttributeValues=encode(
                {":binding": session, ":actor": actor, ":org": org, ":now": now}
            ),
            ReturnValues="ALL_OLD",
        )
        return decode(result["Attributes"])

    async def mailbox(self, org: str, identity: str) -> tuple[Mailbox, dict[str, Any]]:
        item = await self.get(f"mailbox:{org}:{identity}")
        if item is None:
            raise ResourceNotFoundError("Gmail connection is unavailable")
        mailbox = Mailbox.model_validate_json(item["data"])
        cursor = json.loads(item.get("cursor", "{}"))
        if cursor.get("checked"):
            mailbox = mailbox.model_copy(
                update={"last_checked_at": datetime.fromtimestamp(cursor["checked"], UTC)}
            )
        return mailbox, item

    async def list(self, org: str) -> tuple[Mailbox, ...]:
        response = await self.call(
            "query",
            IndexName="Workspace",
            KeyConditionExpression="workspace=:org",
            ExpressionAttributeValues=encode({":org": org}),
            Limit=20,
        )
        return tuple(
            Mailbox.model_validate_json(decode(item)["data"]) for item in response.get("Items", [])
        )

    async def save(
        self,
        mailbox: Mailbox,
        grant: str | None,
        previous: int | None,
        *,
        cursor: dict[str, Any] | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "pk": f"mailbox:{mailbox.organisation_id}:{mailbox.id}",
            "data": mailbox.model_dump_json(exclude={"last_checked_at"}),
            "rev": mailbox.revision,
            "enabled": mailbox.status == "ready",
            "cursor": json.dumps(
                cursor
                or {
                    "since": int(datetime.now(UTC).timestamp()),
                    "start": int(datetime.now(UTC).timestamp()),
                }
            ),
        }
        if mailbox.status != "disabled":
            item["workspace"] = mailbox.organisation_id
        if grant is not None:
            item["grant"] = grant
        if mailbox.status == "ready":
            item.update(poll_key="mailbox", poll_due=0)
        values: dict[str, Any] = {
            "Item": encode(item),
            "ConditionExpression": "attribute_not_exists(pk)"
            if previous is None
            else "rev=:revision",
        }
        if previous is not None:
            values["ExpressionAttributeValues"] = encode({":revision": previous})
        await self.call("put_item", **values)

    async def due(self, now: int) -> tuple[dict[str, Any], ...]:
        result = await self.call(
            "query",
            IndexName="Polling",
            KeyConditionExpression="poll_key=:key AND poll_due<=:now",
            ExpressionAttributeValues=encode({":key": "mailbox", ":now": now}),
            Limit=10,
        )
        return tuple(decode(item) for item in result.get("Items", []))

    async def claim(self, key: str, revision: int, owner: str, now: int) -> bool:
        try:
            await self.call(
                "update_item",
                Key=encode({"pk": key}),
                UpdateExpression="SET lease_owner=:owner, lease_until=:until",
                ConditionExpression=(
                    "enabled=:yes AND rev=:rev AND "
                    "(attribute_not_exists(lease_until) OR lease_until<:now)"
                ),
                ExpressionAttributeValues=encode(
                    {
                        ":owner": owner,
                        ":until": now + 300,
                        ":yes": True,
                        ":rev": revision,
                        ":now": now,
                    }
                ),
            )
            return True
        except ResourceConflictError:
            return False

    async def checkpoint(
        self,
        box: Mailbox,
        owner: str,
        cursor: dict[str, Any],
        grant: str | None = None,
        *,
        release: bool = False,
    ) -> None:
        names = {"#cursor": "cursor", "#grant": "grant"}
        values: dict[str, Any] = {
            ":cursor": json.dumps(cursor),
            ":owner": owner,
            ":rev": box.revision,
            ":yes": True,
            ":next": int(datetime.now(UTC).timestamp()) + 300,
        }
        update = "SET #cursor=:cursor"
        if grant is not None:
            update += ", #grant=:grant"
            values[":grant"] = grant
        else:
            names.pop("#grant")
        if release:
            update += ", poll_due=:next REMOVE lease_owner, lease_until"
        else:
            values.pop(":next")
        await self.call(
            "update_item",
            Key=encode({"pk": f"mailbox:{box.organisation_id}:{box.id}"}),
            UpdateExpression=update,
            ConditionExpression="rev=:rev AND enabled=:yes AND lease_owner=:owner",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=encode(values),
        )

    async def message(
        self, org: str, box: str, identity: str, value: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        key = f"message:{org}:{box}:{identity}"
        if value is not None:
            await self.call(
                "put_item",
                Item=encode(
                    {
                        "pk": key,
                        "data": json.dumps(value),
                        "expires": int(datetime.now(UTC).timestamp()) + 90 * 86400,
                    }
                ),
            )
        item = await self.get(key)
        return json.loads(item["data"]) if item else None


def encode(value: dict[str, Any]) -> dict[str, Any]:
    serializer = TypeSerializer()
    return {key: serializer.serialize(item) for key, item in value.items()}


def decode(value: dict[str, Any]) -> dict[str, Any]:
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(item) for key, item in value.items()}
