from urllib.parse import quote

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.hosting.client import Client
from connectors.hosting.models import Deployment, Project, Variable


class Render:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def projects(self) -> tuple[Project, ...]:
        result, cursor = [], None
        for _ in range(10):
            data = await self.client.call(
                "GET",
                "/v1/services",
                query={"limit": 100, **({"cursor": cursor} if cursor else {})},
            )
            for entry in data:
                item = entry["service"]
                if item["type"] in {"web_service", "private_service", "background_worker"}:
                    result.append(Project(id=item["id"], name=item["name"]))
            if len(data) < 100:
                return tuple(result)
            cursor = data[-1]["cursor"]
        raise ConnectorError("host-limit", "Select an account with fewer than 1,000 services")

    async def variables(self, project: str) -> tuple[Variable, ...]:
        result: list[Variable] = []
        cursor = None
        for _ in range(10):
            data = await self.client.call(
                "GET",
                f"/v1/services/{quote(project, safe='')}/env-vars",
                query={"limit": 100, **({"cursor": cursor} if cursor else {})},
            )
            result.extend(
                Variable(
                    reference=(
                        f"render://{project}/production/" + quote(item["envVar"]["key"], safe="")
                    ),
                    project_id=project,
                    environment="production",
                    name=item["envVar"]["key"],
                    key_id=item["envVar"]["key"],
                    readable=True,
                )
                for item in data
            )
            if len(data) < 100:
                return tuple(result)
            cursor = data[-1]["cursor"]
        raise ConnectorError("host-limit", "Select a service with fewer than 1,000 variables")

    async def read(self, variable: Variable) -> SecretValue | None:
        data = await self.client.call("GET", self._path(variable))
        value = data.get("value")
        return SecretValue(value.encode()) if isinstance(value, str) else None

    async def write(self, variable: Variable, value: SecretValue) -> None:
        await self.client.call("PUT", self._path(variable), body={"value": value.bytes().decode()})

    async def current(self, variable: Variable) -> Deployment:
        data = await self.client.call(
            "GET",
            f"/v1/services/{quote(variable.project_id, safe='')}/deploys",
            query={"limit": 20},
        )
        selected = next(
            (item["deploy"] for item in data if item["deploy"]["status"] == "live"), None
        )
        if not selected:
            raise ConnectorError("host-deployment", "Service has no live deployment")
        return _deployment(selected)

    async def deploy(self, variable: Variable, previous: Deployment, request_id: str) -> Deployment:
        source = await self.client.call("GET", self._deployment_path(variable, previous.id))
        body = (
            {"commitId": source["commit"]["id"]}
            if source.get("commit", {}).get("id")
            else {"imageUrl": source["image"]["ref"]}
        )
        return _deployment(
            await self.client.call(
                "POST", f"/v1/services/{quote(variable.project_id, safe='')}/deploys", body=body
            )
        )

    async def status(self, variable: Variable, identity: str) -> Deployment:
        data = await self.client.call(
            "GET",
            self._deployment_path(variable, identity),
        )
        return _deployment(data)

    async def restore(self, variable: Variable, identity: str) -> Deployment:
        await self.status(variable, identity)
        return _deployment(
            await self.client.call(
                "POST",
                f"/v1/services/{quote(variable.project_id, safe='')}/rollback",
                body={"deployId": identity},
            )
        )

    @staticmethod
    def _path(variable: Variable) -> str:
        service = quote(variable.project_id, safe="")
        return f"/v1/services/{service}/env-vars/{quote(variable.key_id, safe='')}"

    @staticmethod
    def _deployment_path(variable: Variable, identity: str) -> str:
        service = quote(variable.project_id, safe="")
        return f"/v1/services/{service}/deploys/{quote(identity, safe='')}"


def _deployment(data: dict[str, object]) -> Deployment:
    state = data["status"]
    return Deployment(
        id=str(data["id"]),
        state="ready"
        if state == "live"
        else "failed"
        if state
        in {"build_failed", "update_failed", "canceled", "pre_deploy_failed", "deactivated"}
        else "pending",
    )
