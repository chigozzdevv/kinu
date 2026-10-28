import hashlib
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import httpx
import pytest

from examples.render import app

SECRET = "private-render-sending-key-fixture"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    class Handler(app.Handler):
        key = SECRET

        def log_message(self, format: str, *args: object) -> None:
            return

    monkeypatch.setattr(app, "verify", lambda key: key == SECRET)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_render_workload_returns_nonce_and_fingerprint_without_exposing_key(service: str) -> None:
    nonce = "a" * 32
    with httpx.Client(trust_env=False) as http:
        assert http.get(service + "/health").json() == {"ok": True}
        assert http.get(service + "/probe").status_code == 400
        response = http.get(service + "/probe", headers={"X-Kinu-Probe": nonce})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "ok": True,
        "nonce": nonce,
        "fingerprints": {"RESEND_API_KEY": hashlib.sha256(SECRET.encode()).hexdigest()},
    }
    assert SECRET not in response.text


def test_render_workload_requires_sending_only_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 200

        def read(self, limit: int) -> bytes:
            return b'{"object":"list","data":[]}'

    class Connection:
        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "GET" and path == "/inboxes?limit=1"
            assert headers["Authorization"] == "Bearer " + SECRET

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            return

    monkeypatch.setattr(app, "HTTPSConnection", lambda *args, **kwargs: Connection())
    with pytest.raises(ValueError, match="sending-only"):
        app.verify(SECRET)
