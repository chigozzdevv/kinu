import hashlib
import json

import httpx

DOMAIN = "0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c"


class Platform:
    def __init__(self, *, unknown: bool = False, value: str = "setup-required") -> None:
        self.unknown, self.value, self.loaded = unknown, value, value
        self.keys: dict[str, dict[str, str]] = {}
        self.created: list[str] = []
        self.revoked: list[str] = []
        self.deployments = 0
        self.current = "initial"

    def response(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "api.resend.com":
            if path == "/inboxes":
                valid = any(
                    "Bearer " + item["token"] == request.headers["authorization"]
                    for item in self.keys.values()
                )
                return httpx.Response(
                    401 if valid else 400,
                    json={
                        "name": "restricted_api_key" if valid else "validation_error",
                        "message": "This API key is restricted to only send emails."
                        if valid
                        else "API key is invalid",
                    },
                )
            assert request.headers["authorization"] == "Bearer management-fixture"
            if path == "/api-keys" and request.method == "POST":
                body = json.loads(request.content)
                assert body["permission"] == "sending_access" and body["domain_id"] == DOMAIN
                key = f"0b0ca8e1-fd3f-4edf-b762-{len(self.created) + 1:012d}"
                self.created.append(key)
                item = {"id": key, "name": body["name"], "token": "re_private-fixture-" + key}
                self.keys[key] = item
                if self.unknown:
                    raise httpx.ReadTimeout("Unknown create result")
                return httpx.Response(201, json=item)
            if path == "/api-keys" and request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "has_more": False,
                        "data": [
                            {
                                "id": item["id"],
                                "name": item["name"],
                                "created_at": "2026-10-28 20:46:24.150023+00",
                                "last_used_at": "2026-10-28 20:53:16.599452+00",
                            }
                            for item in self.keys.values()
                        ],
                    },
                )
            if path.startswith("/api-keys/") and request.method == "DELETE":
                key = path.rsplit("/", 1)[1]
                self.keys.pop(key)
                self.revoked.append(key)
                return httpx.Response(200, json={"id": key})
            raise AssertionError(str(request.url))
        if request.url.host == "kinu-check.workload.example":
            valid = any(item["token"] == self.loaded for item in self.keys.values())
            return httpx.Response(
                200,
                json={
                    "ok": valid,
                    "nonce": request.headers["x-kinu-probe"],
                    "fingerprints": {
                        "RESEND_API_KEY": hashlib.sha256(self.loaded.encode()).hexdigest()
                    },
                    **({"state": "provider-rejected"} if not valid else {}),
                },
            )
        assert request.url.host == "api.render.com"
        assert request.headers["authorization"] == "Bearer host-management-fixture"
        if path == "/v1/services":
            return httpx.Response(
                200,
                json=[
                    {
                        "service": {
                            "id": "app",
                            "name": "kinu-check",
                            "type": "web_service",
                        },
                        "cursor": "app",
                    }
                ],
            )
        if path == "/v1/services/app/env-vars":
            return httpx.Response(
                200,
                json=[
                    {
                        "envVar": {
                            "key": "RESEND_API_KEY",
                            "value": self.value,
                        },
                        "cursor": "variable",
                    }
                ],
            )
        if path == "/v1/services/app/env-vars/RESEND_API_KEY":
            if request.method == "PUT":
                self.value = json.loads(request.content)["value"]
            return httpx.Response(200, json={"key": "RESEND_API_KEY", "value": self.value})
        if path == "/v1/services/app/deploys":
            if request.method == "POST":
                assert json.loads(request.content) == {"commitId": "pinned-source"}
                self.deployments += 1
                self.loaded, self.current = self.value, "deployment-" + str(self.deployments)
                return httpx.Response(201, json={"id": self.current, "status": "live"})
            return httpx.Response(200, json=[{"deploy": {"id": self.current, "status": "live"}}])
        if path.startswith("/v1/services/app/deploys/"):
            return httpx.Response(
                200,
                json={
                    "id": path.rsplit("/", 1)[1],
                    "status": "live",
                    "commit": {"id": "pinned-source"},
                },
            )
        raise AssertionError(str(request.url))
