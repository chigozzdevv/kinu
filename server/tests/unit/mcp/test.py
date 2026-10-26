from contextlib import AbstractAsyncContextManager

from core.mcpclient import authenticated_streamable_http


def test_authenticated_streamable_http_implements_transport_contract() -> None:
    transport = authenticated_streamable_http(
        "https://broker.kinu.example/mcp", {"Authorization": "Bearer identity-token"}
    )
    assert isinstance(transport, AbstractAsyncContextManager)
