import re
from typing import Any
from urllib.parse import urljoin

import httpx
from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.http.client import _dig, _metadata_value, _value_headers
from connectors.http.transport import PublicTransport
from contracts.playbook import BrowserProbe


class Probe:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http

    async def check(self, config: BrowserProbe, value: SecretValue) -> tuple[int, dict[str, Any]]:
        client = self.http or httpx.AsyncClient(transport=PublicTransport(), trust_env=False)
        try:
            async with client.stream(
                "GET",
                urljoin(config.base_url, config.operation.path),
                headers=_value_headers(value, config.auth),
                params=config.operation.query or None,
                timeout=10,
                follow_redirects=False,
            ) as response:
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 65536:
                        raise ConnectorError(
                            "browser-probe", "Credential proof response is too large"
                        )
                if response.status_code in {401, 403}:
                    return response.status_code, {}
                if response.status_code not in config.operation.success_statuses:
                    raise ConnectorError("browser-probe", "Credential proof failed")
            import json

            data = json.loads(body)
            identity = _dig(data, config.operation.provider_id_field or "")
            if not isinstance(identity, str) or not re.fullmatch(
                r"[A-Za-z0-9_:-][A-Za-z0-9_.:-]{0,255}", identity
            ):
                raise ValueError
            result: dict[str, Any] = {
                "provider_id": identity,
                "kind": config.kind,
                "scopes": [],
                "status": "active",
                "disabled": False,
            }
            for name, path in config.operation.metadata_fields.items():
                found = _dig(data, path)
                if found is not None:
                    result[name] = _metadata_value(name, found)
            return response.status_code, result
        except (ValueError, TypeError, AttributeError, httpx.HTTPError):
            raise ConnectorError(
                "browser-probe", "Credential identity could not be verified"
            ) from None
        finally:
            if self.http is None:
                await client.aclose()
