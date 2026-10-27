import hashlib
import os
from typing import Literal

from connectors.aws import AwsApi
from connectors.base import SecretValue
from contracts import Contract, Identifier
from pydantic import Field

from demo.store import ProviderStore


class AdminRequest(Contract):
    operation: Literal["create", "metadata", "revoke"]
    organisation_id: Identifier
    request_id: Identifier | None = None
    provider_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")
    expected_uses: int | None = Field(default=None, ge=0)


async def administer(aws: AwsApi, event: object) -> dict[str, object]:
    request = AdminRequest.model_validate(event)
    org = os.environ["KINU_DEMO_ORGANISATION"]
    if request.organisation_id != org:
        raise ValueError("demo provider belongs to another workspace")
    region = os.environ["AWS_REGION"]
    seed = await aws.call(
        "secretsmanager", "get_secret_value", region, SecretId=os.environ["KINU_DEMO_SEED"]
    )
    store = ProviderStore(aws, region, os.environ["KINU_DEMO_TABLE"], seed["SecretString"].encode())
    if request.operation == "metadata":
        if request.provider_id:
            return {"key": await store.get(org, request.provider_id), "demo": True}
        return {"keys": await store.list(org), "demo": True}
    if request.operation == "revoke":
        if not request.provider_id:
            raise ValueError("a provider key identity is required")
        await store.revoke(org, request.provider_id, request.expected_uses)
        return {"provider_id": request.provider_id, "state": "revoked", "demo": True}
    if not request.request_id:
        raise ValueError("an idempotent creation request is required")
    provider_id, raw = await store.create(org, request.request_id)
    value = SecretValue(raw.encode())
    raw = ""
    token = hashlib.sha256(f"{org}\0{request.request_id}".encode()).hexdigest()
    try:
        result = await aws.call(
            "secretsmanager",
            "put_secret_value",
            region,
            SecretId=os.environ["KINU_DEMO_KEY"],
            ClientRequestToken=token,
            SecretBinary=value.bytes(),
            VersionStages=[f"kinu-candidate-{token[:24]}"],
        )
    finally:
        value.clear()
    return {
        "provider_id": provider_id,
        "secret_reference": result["ARN"] + "/versions/" + result["VersionId"],
        "scopes": ["demo:read"],
        "demo": True,
    }
