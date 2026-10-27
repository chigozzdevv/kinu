import asyncio
import os
import re
from typing import Any

import httpx
from connectors.aws import AwsClient
from connectors.base import SecretValue


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    del context
    return asyncio.run(check(event))


async def check(event: dict[str, Any]) -> dict[str, Any]:
    region = os.environ["AWS_REGION"]
    resource = os.environ["KINU_DEMO_KEY"]
    organisation = os.environ.get("KINU_DEMO_ORGANISATION", "")
    version = event.get("candidate_version") or os.environ.get("KINU_SECRET_VERSION", "")
    if (
        not organisation
        or not isinstance(version, str)
        or not re.fullmatch(r"[A-Za-z0-9-]{32,64}", version)
    ):
        return {"ok": False, "state": "setup-required", "demo": True}
    response = await AwsClient().call(
        "secretsmanager", "get_secret_value", region, SecretId=resource, VersionId=version
    )
    value = SecretValue(response.get("SecretBinary") or str(response["SecretString"]).encode())
    try:
        async with httpx.AsyncClient(timeout=4, follow_redirects=False) as client:
            for attempt in range(3):
                try:
                    result = await client.get(
                        os.environ["KINU_PROVIDER_URL"] + "/api/check",
                        headers={
                            "Authorization": "Bearer " + value.bytes().decode(),
                            "x-demo-organisation": organisation,
                        },
                    )
                except httpx.TransportError:
                    if attempt == 2:
                        return {"ok": False, "state": "provider-unavailable", "demo": True}
                else:
                    if result.status_code < 500 and result.status_code not in {408, 429}:
                        break
                    if attempt == 2:
                        return {"ok": False, "state": "provider-unavailable", "demo": True}
                await asyncio.sleep(0.3 * (attempt + 1))
        if result.status_code in {401, 403}:
            return {"ok": False, "state": "provider-rejected", "demo": True}
        if result.status_code != 200:
            return {"ok": False, "state": "provider-unavailable", "demo": True}
        payload = result.json()
        identity = payload.get("provider_id")
        if (
            payload.get("ok") is not True
            or not isinstance(identity, str)
            or not re.fullmatch(r"[a-f0-9]{24}", identity)
        ):
            return {"ok": False, "state": "invalid-provider-result", "demo": True}
        return {"ok": True, "provider_id": identity, "secret_version": version, "demo": True}
    finally:
        value.clear()
