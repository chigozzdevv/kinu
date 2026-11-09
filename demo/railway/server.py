import os
from http.server import ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path

from status import handler as StatusHandler

ROOT = Path(__file__).resolve().parent


class Handler(StatusHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/status":
            super().do_GET()
            return
        name = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
        target = (ROOT / name).resolve()
        if not str(target).startswith(str(ROOT) + os.sep) or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
