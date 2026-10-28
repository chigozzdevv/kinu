import hashlib
import json
import os
import re
import threading
import time
from http.client import HTTPException, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from connectors.resend.check import classify


def verify(key: str) -> bool:
    connection = HTTPSConnection("api.resend.com", timeout=10)
    try:
        connection.request(
            "GET",
            "/inboxes?limit=1",
            headers={
                "Authorization": "Bearer " + key,
                "User-Agent": "kinu-check/0.1",
            },
        )
        response = connection.getresponse()
        body = response.read(16385)
        if len(body) > 16384:
            raise ValueError("Provider response exceeded its limit")
        state = classify(response.status, json.loads(body))
    finally:
        connection.close()
    if state == "sending_access":
        return True
    if state == "rejected":
        return False
    raise ValueError("Provider did not confirm a sending-only key")


class Handler(BaseHTTPRequestHandler):
    key = os.environ.get("RESEND_API_KEY", "")
    lock = threading.Lock()
    next_check = 0.0

    def reply(self, status: int, data: dict[str, object]) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self.reply(200, {"ok": True})
            return
        if path != "/probe":
            self.reply(404, {"error": "Not found"})
            return
        nonce = self.headers.get("X-Kinu-Probe", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32}", nonce):
            self.reply(400, {"error": "A probe nonce is required"})
            return
        if not self.key or self.key == "setup-required":
            self.reply(503, {"state": "setup-required"})
            return
        with self.lock:
            now = time.monotonic()
            if now < type(self).next_check:
                self.reply(429, {"state": "busy"})
                return
            type(self).next_check = now + 0.6
        try:
            valid = verify(self.key)
        except (HTTPException, ValueError, OSError):
            self.reply(503, {"state": "provider-unavailable"})
            return
        self.reply(
            200,
            {
                "ok": valid,
                "nonce": nonce,
                "fingerprints": {"RESEND_API_KEY": hashlib.sha256(self.key.encode()).hexdigest()},
                **({"state": "provider-rejected"} if not valid else {}),
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()
