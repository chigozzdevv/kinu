import asyncio
import ipaddress
import socket

import httpx

from connectors.base.errors import ConnectorError


class PublicTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self._transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.scheme != "https" or not host:
            raise ConnectorError("provider-origin", "provider requires a public HTTPS endpoint")
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                host, request.url.port or 443, type=socket.SOCK_STREAM
            )
        except OSError as error:
            raise ConnectorError(
                "provider-dns", "provider hostname could not be resolved"
            ) from error
        targets = {str(address[4][0]) for address in addresses}
        if not targets or any(not public_address(address) for address in targets):
            raise ConnectorError(
                "provider-origin", "provider must resolve only to public addresses"
            )
        extensions = request.extensions | {"sni_hostname": host}
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=sorted(targets)[0]),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._transport.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self._transport.aclose()


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast
