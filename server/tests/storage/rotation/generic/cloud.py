import io
import json
from typing import Any

import httpx
from connectors.aws import Role
from connectors.base.errors import ConnectorError

SECRET = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu-demo-key-ABC123"
OLD_REF = SECRET + "/versions/" + "1" * 64
FUNCTION = "arn:aws:lambda:eu-west-2:123456789012:function:kinu-demo-consumer"


class Cloud:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.provider = {
            "a" * 24: {"name": "initial", "value": "fixture-old-value", "active": True}
        }
        self.secrets = {
            "1" * 64: (b"fixture-old-value", ["AWSCURRENT"]),
            "2" * 64: (b"fixture-management", ["AWSCURRENT"]),
        }
        self.environments = {"1": {"PROVIDER_REF": OLD_REF, "UNRELATED": "keep"}}
        self.latest = dict(self.environments["1"])
        self.alias, self.revision = "1", 1
        self.alias_revision = 1
        self.operations: list[str] = []

    async def http(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scopes"):
            token = request.headers["authorization"].removeprefix("Bearer ")
            key = next(
                (
                    identity
                    for identity, item in self.provider.items()
                    if item["value"] == token and item["active"]
                ),
                None,
            )
            return httpx.Response(200, json={"id": key}) if key else httpx.Response(401)
        assert request.headers["authorization"] == "Bearer fixture-management"
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "api_key_id": identity,
                            "name": item["name"],
                            "kind": "api-key",
                            "status": "active" if item["active"] else "disabled",
                            "disabled": not item["active"],
                            "scopes": ["demo:read"],
                            "api_key": item["value"],
                        }
                        for identity, item in self.provider.items()
                    ]
                },
            )
        if request.method == "POST":
            data = json.loads(request.content)
            self.provider["replacement"] = {
                "name": data["name"],
                "value": "fixture-new-value",
                "active": True,
            }
            self.operations.append("create")
            return httpx.Response(
                201, json={"api_key_id": "replacement", "api_key": "fixture-new-value"}
            )
        self.provider[request.url.path.split("/")[-1]]["active"] = False
        self.operations.append("revoke")
        return httpx.Response(204)

    async def call(
        self,
        service: str,
        operation: str,
        region: str,
        *,
        role: Role | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        assert role and role.policy and ":role/kinu/aws_" in role.arn
        if operation == "get_caller_identity":
            return {"Account": "123456789012"}
        if operation == "describe_secret":
            return {"ARN": parameters["SecretId"], "Name": "Selected key"}
        if operation == "get_function_configuration":
            qualifier = parameters.get("Qualifier")
            return {
                "FunctionArn": FUNCTION,
                "FunctionName": "consumer",
                "Role": "arn:aws:iam::123456789012:role/consumer",
                "Environment": {
                    "Variables": self.environments[qualifier] if qualifier else self.latest
                },
                "RevisionId": str(self.revision),
                "CodeSha256": "unchanged",
                "LastUpdateStatus": "Successful",
            }
        if operation == "get_alias":
            return {"FunctionVersion": self.alias, "RevisionId": str(self.alias_revision)}
        if operation == "update_function_configuration":
            self.latest = dict(parameters["Environment"]["Variables"])
            self.revision += 1
            return {}
        if operation == "publish_version":
            self.environments["2"] = dict(self.latest)
            return {"Version": "2"}
        if operation == "update_alias":
            self.alias = parameters["FunctionVersion"]
            self.alias_revision += 1
            return {"FunctionVersion": self.alias, "RevisionId": str(self.alias_revision)}
        if operation == "get_secret_value":
            return {"SecretBinary": self.secrets[parameters["VersionId"]][0]}
        if operation == "put_secret_value":
            token = parameters["ClientRequestToken"]
            self.secrets[token] = (parameters["SecretBinary"], parameters["VersionStages"])
            return {"VersionId": token}
        if operation == "list_secret_version_ids":
            return {
                "Versions": [
                    {"VersionId": version, "VersionStages": stages}
                    for version, (_, stages) in self.secrets.items()
                    if version != "2" * 64
                ]
            }
        if operation == "update_secret_version_stage":
            for version, (_value, stages) in list(self.secrets.items()):
                stage = parameters["VersionStage"]
                if version == parameters.get("RemoveFromVersionId") and stage in stages:
                    stages.remove(stage)
                if version == parameters.get("MoveToVersionId") and stage not in stages:
                    stages.append(stage)
            return {}
        if operation == "invoke":
            qualifier = self.alias if parameters["Qualifier"] == "live" else parameters["Qualifier"]
            probe = json.loads(parameters["Payload"])["kinu"]
            reference = (
                probe.get("secret_reference") or self.environments[qualifier]["PROVIDER_REF"]
            )
            value = self.secrets[reference.split("/versions/")[1]][0].decode()
            identity = next(
                (
                    identity
                    for identity, item in self.provider.items()
                    if item["value"] == value and item["active"]
                ),
                None,
            )
            if self.fail and qualifier == "2":
                raise ConnectorError("test-health", "candidate failed")
            result = {"challenge": probe["challenge"], "healthy": bool(identity)}
            result.update(
                {"provider_id": identity, "secret_reference": reference}
                if identity
                else {"reason": "credential-rejected"}
            )
            return {"Payload": io.BytesIO(json.dumps({"kinu": result}).encode())}
        raise AssertionError(operation)
