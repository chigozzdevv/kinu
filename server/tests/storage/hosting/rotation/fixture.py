import hashlib
import json
from urllib.parse import unquote

import httpx

OLD = "old-workload-fixture-private"
NEW = "new-workload-fixture-private"
MANAGEMENT = "management-fixture-private"


class Platform:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.keys = {"old": {"value": OLD, "active": True}}
        self.variables = {"project-one": OLD, "project-two": OLD}
        self.current = {project: project + "-old" for project in self.variables}
        self.loaded = dict(self.variables)
        self.deployments = dict.fromkeys(self.current.values(), OLD)
        self.revoked: list[str] = []
        self.writes: list[str] = []

    def response(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "issuer.example":
            if path == "/api_keys" and request.method == "GET":
                assert request.headers["authorization"] == "Bearer " + MANAGEMENT
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "api_key_id": key,
                                "name": "API key",
                                "kind": "api-key",
                                "scopes": ["read"],
                                "status": "active" if item["active"] else "disabled",
                                "disabled": not item["active"],
                            }
                            for key, item in self.keys.items()
                        ]
                    },
                )
            if path == "/api_keys" and request.method == "POST":
                self.keys["new"] = {"value": NEW, "active": True}
                return httpx.Response(201, json={"api_key_id": "new", "api_key": NEW})
            if path.startswith("/api_keys/") and request.method == "DELETE":
                key = unquote(path.rsplit("/", 1)[1])
                self.keys[key]["active"] = False
                self.revoked.append(key)
                return httpx.Response(204)
            if path == "/scopes":
                value = request.headers["authorization"].removeprefix("Bearer ")
                item = next(
                    (
                        (key, item)
                        for key, item in self.keys.items()
                        if item["value"] == value and item["active"]
                    ),
                    None,
                )
                return httpx.Response(200, json={"id": item[0]}) if item else httpx.Response(401)
            raise AssertionError(str(request.url))
        if request.url.host.endswith(".workload.example"):
            project = request.url.host.removesuffix(".workload.example")
            value = self.loaded[project]
            ok = any(item["value"] == value and item["active"] for item in self.keys.values())
            if self.failure == "proof" and project == "project-two" and value == NEW:
                ok = False
            return httpx.Response(
                200,
                json={
                    "ok": ok,
                    "nonce": request.headers["x-kinu-probe"],
                    "state": "ready" if ok else "provider-rejected",
                    "fingerprints": {"API_KEY": hashlib.sha256(value.encode()).hexdigest()},
                },
            )
        assert request.url.host == "api.render.com"
        assert request.headers["authorization"] == "Bearer host-management-fixture"
        if path == "/v1/services":
            return httpx.Response(
                200,
                json=[
                    {"service": {"id": item, "name": item, "type": "web_service"}, "cursor": item}
                    for item in self.variables
                ],
            )
        parts = path.split("/")
        project = parts[3]
        if path.endswith("/env-vars"):
            return httpx.Response(
                200,
                json=[
                    {
                        "envVar": {"key": "API_KEY", "value": self.variables[project]},
                        "cursor": "one",
                    }
                ],
            )
        if "/env-vars/" in path:
            assert path.endswith("/API_KEY")
            if request.method == "PUT":
                body = json.loads(request.content)
                assert set(body) == {"value"}
                self.variables[project] = body["value"]
                self.writes.append(project)
            return httpx.Response(200, json={"key": "API_KEY", "value": self.variables[project]})
        if path.endswith("/deploys") and request.method == "GET":
            return httpx.Response(
                200, json=[{"deploy": {"id": self.current[project], "status": "live"}}]
            )
        if "/deploys/" in path:
            deployment = parts[-1]
            return httpx.Response(
                200,
                json={
                    "id": deployment,
                    "status": "build_in_progress"
                    if self.failure in {"pending", "pending-unknown"}
                    and deployment.endswith("-new")
                    else "live"
                    if self.failure == "ready-not-live" and deployment.endswith("-new")
                    else "live"
                    if self.current[project] == deployment
                    else "deactivated",
                    "commit": {"id": "pinned-source"},
                },
            )
        if path.endswith("/deploys") and request.method == "POST":
            assert json.loads(request.content) == {"commitId": "pinned-source"}
            candidate = project + "-new"
            if self.failure in {"pending", "pending-unknown", "ready-not-live"}:
                self.deployments[candidate] = self.variables[project]
                if self.failure == "pending-unknown":
                    return httpx.Response(500)
                return httpx.Response(200, json={"id": candidate, "status": "created"})
            self.current[project] = candidate
            self.loaded[project] = self.variables[project]
            self.deployments[candidate] = self.loaded[project]
            if self.failure == "unknown" and project == "project-two":
                return httpx.Response(500, text=NEW)
            return httpx.Response(200, json={"id": candidate, "status": "live"})
        if path.endswith("/rollback"):
            deployment = json.loads(request.content)["deployId"]
            self.current[project], self.loaded[project] = deployment, self.deployments[deployment]
            return httpx.Response(200, json={"id": deployment, "status": "live"})
        raise AssertionError(str(request.url))
