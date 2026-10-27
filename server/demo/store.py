import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from connectors.aws import AwsApi
from connectors.aws.client import AwsError

from demo.auth import signature


class ProviderStore:
    def __init__(self, aws: AwsApi, region: str, table: str, seed: bytes) -> None:
        self._aws, self._region, self._table, self._seed = aws, region, table, seed

    async def create(self, org: str, request_id: str) -> tuple[str, str]:
        key_id = hashlib.sha256(f"{org}\0{request_id}".encode()).hexdigest()[:24]
        value = f"kinu_demo_{key_id}_{signature(self._seed, 'credential', org + ':' + key_id)}"
        item = {
            "org": {"S": org},
            "id": {"S": key_id},
            "digest": {"S": hashlib.sha256(value.encode()).hexdigest()},
            "state": {"S": "active"},
            "created": {"S": datetime.now(UTC).isoformat()},
            "uses": {"N": "0"},
        }
        try:
            await self._call("put_item", Item=item, ConditionExpression="attribute_not_exists(id)")
        except AwsError as error:
            if error.code != "ConditionalCheckFailedException":
                raise
            current = await self.get(org, key_id)
            if not current or current["state"] != "active":
                raise ValueError("this creation request already belongs to a retired key") from None
        return key_id, value

    async def get(self, org: str, key_id: str) -> dict[str, Any] | None:
        result = await self._call("get_item", Key=_key(org, key_id), ConsistentRead=True)
        item = result.get("Item")
        return _metadata(item) if item else None

    async def list(self, org: str) -> tuple[dict[str, Any], ...]:
        result = await self._call(
            "query",
            KeyConditionExpression="#org = :org",
            ExpressionAttributeNames={"#org": "org"},
            ExpressionAttributeValues={":org": {"S": org}},
            ConsistentRead=True,
            Limit=100,
        )
        return tuple(_metadata(item) for item in result.get("Items", []))

    async def revoke(self, org: str, key_id: str, expected_uses: int | None = None) -> None:
        current = await self.get(org, key_id)
        if current and current["state"] == "revoked":
            return
        condition = "attribute_exists(id)"
        names = {"#state": "state"}
        values = {
            ":state": {"S": "revoked"},
            ":now": {"S": datetime.now(UTC).isoformat()},
        }
        if expected_uses is not None:
            condition += " AND #state = :active AND #uses = :uses"
            names["#uses"] = "uses"
            values.update({":active": {"S": "active"}, ":uses": {"N": str(expected_uses)}})
        await self._call(
            "update_item",
            Key=_key(org, key_id),
            UpdateExpression="SET #state = :state, retired = :now",
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    async def check(self, org: str, value: str) -> dict[str, Any] | None:
        if not value.startswith("kinu_demo_") or len(value) > 100:
            return None
        key_id = value[10:].partition("_")[0]
        if len(key_id) != 24 or any(char not in "0123456789abcdef" for char in key_id):
            return None
        result = await self._call("get_item", Key=_key(org, key_id), ConsistentRead=True)
        item = result.get("Item")
        if (
            not item
            or item["state"]["S"] != "active"
            or not hmac.compare_digest(
                item["digest"]["S"], hashlib.sha256(value.encode()).hexdigest()
            )
        ):
            return None
        try:
            await self._call(
                "update_item",
                Key=_key(org, key_id),
                UpdateExpression="SET last_used = :now ADD uses :one",
                ConditionExpression="#state = :active",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":now": {"S": datetime.now(UTC).isoformat()},
                    ":one": {"N": "1"},
                    ":active": {"S": "active"},
                },
            )
        except AwsError as error:
            if error.code == "ConditionalCheckFailedException":
                return None
            raise
        return _metadata(item)

    async def _call(self, operation: str, **parameters: Any) -> dict[str, Any]:
        return await self._aws.call(
            "dynamodb", operation, self._region, TableName=self._table, **parameters
        )


def _key(org: str, key_id: str) -> dict[str, Any]:
    return {"org": {"S": org}, "id": {"S": key_id}}


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"]["S"],
        "state": item["state"]["S"],
        "created_at": item["created"]["S"],
        "last_used": item.get("last_used", {}).get("S"),
        "uses": int(item.get("uses", {}).get("N", "0")),
        "scopes": ["demo:read"],
    }
