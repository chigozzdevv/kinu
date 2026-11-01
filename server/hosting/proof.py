import hashlib
import json
import secrets
from urllib.parse import urlsplit, urlunsplit

import httpx
from connectors.base.errors import ConnectorError
from connectors.http.transport import PublicTransport
from contracts import ConsumerService


class Proof:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http

    async def check(
        self,
        service: ConsumerService,
        variable: str,
        fingerprint: str | None,
        *,
        origin: str | None = None,
        rejected: bool = False,
        provider_id: str | None = None,
    ) -> str:
        return await self.endpoint(
            service.endpoint,
            variable,
            fingerprint,
            origin=origin,
            rejected=rejected,
            provider_id=provider_id,
        )

    async def endpoint(
        self,
        url: str | None,
        variable: str,
        fingerprint: str | None,
        *,
        origin: str | None = None,
        rejected: bool = False,
        provider_id: str | None = None,
    ) -> str:
        if not url:
            raise ConnectorError("host-proof", "Configure the workload health URL")
        endpoint = urlsplit(url)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.fragment
        ):
            raise ConnectorError("host-proof", "Configure an HTTPS health URL without credentials")
        if origin:
            deployment = urlsplit(origin)
            if (
                deployment.scheme != "https"
                or deployment.username
                or deployment.password
                or not deployment.hostname
            ):
                raise ConnectorError("host-proof", "Deployment has no valid health origin")
            endpoint = endpoint._replace(scheme="https", netloc=deployment.netloc)
        nonce = secrets.token_urlsafe(24)
        if self.http:
            return await self._request(
                self.http, urlunsplit(endpoint), variable, fingerprint, nonce, rejected, provider_id
            )
        async with httpx.AsyncClient(transport=PublicTransport(), trust_env=False) as client:
            return await self._request(
                client, urlunsplit(endpoint), variable, fingerprint, nonce, rejected, provider_id
            )

    async def _request(
        self,
        http: httpx.AsyncClient,
        url: str,
        variable: str,
        fingerprint: str | None,
        nonce: str,
        rejected: bool,
        provider_id: str | None,
    ) -> str:
        try:
            async with http.stream(
                "GET",
                url,
                headers={"X-Kinu-Probe": nonce, "Cache-Control": "no-store"},
                timeout=15,
                follow_redirects=False,
            ) as response:
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 16384:
                        raise ValueError
                if not response.is_success:
                    raise ValueError
            result = json.loads(payload)
            actual = result.get("fingerprints", {}).get(variable)
            if (
                result.get("nonce") != nonce
                or result.get("ok") is not (not rejected)
                or not isinstance(actual, str)
                or len(actual) != 64
                or any(value not in "0123456789abcdef" for value in actual)
                or (fingerprint and not secrets.compare_digest(actual, fingerprint))
                or (rejected and result.get("state") != "provider-rejected")
                or (provider_id and result.get("provider_ids", {}).get(variable) != provider_id)
            ):
                raise ValueError
            return actual
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise ConnectorError(
                "host-proof", "Workload did not prove the selected credential is in use"
            ) from None


def fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
