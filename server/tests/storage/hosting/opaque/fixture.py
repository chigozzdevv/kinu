import hashlib
import json

import httpx
from tests.storage.hosting.rotation.fixture import NEW, Platform


class Opaque(Platform):
    def response(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "issuer.example":
            return super().response(request)
        if host.endswith(".workload.example"):
            project = host.removesuffix(".workload.example")
            value = self.loaded[project]
            return self.proof(request, value)
        if host.endswith(".deployment.example"):
            return self.proof(request, self.deployments[host.removesuffix(".deployment.example")])
        assert host == "api.vercel.com"
        if path == "/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "host-management-fixture"})
        if path == "/v9/projects":
            return httpx.Response(
                200, json={"projects": [{"id": key, "name": key} for key in self.variables]}
            )
        if path.startswith("/v9/projects/") or path.startswith("/v10/projects/"):
            project = path.split("/")[3]
            if path.endswith("/env"):
                return httpx.Response(
                    200,
                    json={
                        "envs": [
                            {
                                "id": "key-one",
                                "key": "API_KEY",
                                "target": ["production"],
                                "type": "sensitive",
                            }
                        ]
                    },
                )
            if "/env/" in path:
                assert request.method == "PATCH"
                self.variables[project] = json.loads(request.content)["value"]
                self.writes.append(project)
                return httpx.Response(200, json={"id": "key-one"})
            return httpx.Response(
                200,
                json={
                    "name": project,
                    "targets": {"production": self.deployment(project, self.current[project])},
                },
            )
        if path.startswith("/v13/deployments/"):
            deployment = path.rsplit("/", 1)[1]
            project = deployment.rsplit("-", 1)[0]
            return httpx.Response(200, json=self.deployment(project, deployment))
        if path == "/v13/deployments":
            body = json.loads(request.content)
            project = body["project"]
            assert body["deploymentId"] == project + "-old"
            deployment = project + "-new"
            self.current[project], self.loaded[project] = deployment, self.variables[project]
            self.deployments[deployment] = self.loaded[project]
            return httpx.Response(200, json=self.deployment(project, deployment))
        if path.startswith("/v1/projects/") and "/rollback/" in path:
            project, deployment = path.split("/")[3], path.rsplit("/", 1)[1]
            self.current[project], self.loaded[project] = deployment, self.deployments[deployment]
            return httpx.Response(200, json={})
        raise AssertionError(str(request.url))

    @staticmethod
    def deployment(project: str, identity: str) -> dict[str, str]:
        return {
            "id": identity,
            "projectId": project,
            "readyState": "READY",
            "url": identity + ".deployment.example",
        }

    def proof(self, request: httpx.Request, value: str) -> httpx.Response:
        key = next(key for key, item in self.keys.items() if item["value"] == value)
        ok = self.keys[key]["active"] and not (self.failure == "proof" and value == NEW)
        return httpx.Response(
            200,
            json={
                "ok": ok,
                "nonce": request.headers["x-kinu-probe"],
                "state": "ready" if ok else "provider-rejected",
                "fingerprints": {"API_KEY": hashlib.sha256(value.encode()).hexdigest()},
                "provider_ids": {"API_KEY": "wrong" if self.failure == "identity" else key},
            },
        )
