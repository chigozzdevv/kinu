import hashlib
import json
import os
import re
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


KEY_NAME = "RESEND_API_KEY"
MAX_BODY = 16_384
NONCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{32}\Z")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _classify(status: int, payload: Any) -> str:
    if not isinstance(payload, dict):
        return "unavailable"
    if status == 200 and payload.get("object") == "list" and isinstance(payload.get("data"), list):
        return "full_access"
    name, message = payload.get("name"), payload.get("message")
    if (
        status == 401
        and name == "restricted_api_key"
        and isinstance(message, str)
        and message.removesuffix(".") == "This API key is restricted to only send emails"
    ):
        return "sending_access"
    if (
        status in {400, 401}
        and name == "validation_error"
        and message == "API key is invalid"
    ) or (
        status == 403
        and (
            (name == "restricted_api_key" and message == "API key is not active")
            or name == "suspended_api_key"
        )
    ):
        return "rejected"
    return "unavailable"


def _resend_access(key: str) -> str:
    request = Request(
        "https://api.resend.com/inboxes?limit=1",
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": "kinu-vercel-demo/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=8) as response:
            status = response.status
            raw = response.read(MAX_BODY + 1)
    except HTTPError as error:
        try:
            status = error.code
            raw = error.read(MAX_BODY + 1)
        except OSError:
            return "unavailable"
        finally:
            error.close()
    except (HTTPException, URLError, TimeoutError, OSError):
        return "unavailable"
    if len(raw) > MAX_BODY:
        return "unavailable"
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unavailable"
    return _classify(status, payload)


class handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        key = os.environ.get(KEY_NAME, "")
        nonce = self.headers.get("X-Kinu-Probe")
        if nonce is not None:
            self._probe(key, nonce)
            return
        self._status(key)

    def _status(self, key: str) -> None:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest() if key else None
        self._reply(
            200,
            {
                "deployment_id": os.environ.get("VERCEL_DEPLOYMENT_ID")
                or os.environ.get("RAILWAY_DEPLOYMENT_ID"),
                "fingerprint": digest[:12] if digest else None,
            },
        )

    def _probe(self, key: str, nonce: str) -> None:
        if not NONCE_PATTERN.fullmatch(nonce):
            self._reply(400, {"error": "Invalid probe nonce"})
            return
        if not key or key == "setup-required":
            self._reply(503, {"state": "setup-required"})
            return

        access = _resend_access(key)
        if access == "unavailable":
            self._reply(503, {"state": "provider-unavailable"})
            return

        accepted = access in {"sending_access", "full_access"}
        self._reply(
            200,
            {
                "ok": accepted,
                "nonce": nonce,
                "fingerprints": {KEY_NAME: hashlib.sha256(key.encode("utf-8")).hexdigest()},
                **({"state": "provider-rejected"} if not accepted else {}),
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        return
