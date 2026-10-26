from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def authenticated_streamable_http(
    url: str,
    headers: dict[str, str],
) -> AsyncIterator[Any]:
    async with (
        httpx2.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx2.Timeout(30, read=300),
        ) as client,
        streamable_http_client(url, http_client=client) as streams,
    ):
        yield streams
