from urllib.parse import quote

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.hosting.client import Client
from connectors.hosting.models import Deployment, DeploymentProgress, Project, Variable


class Vercel:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def projects(self) -> tuple[Project, ...]:
        result: list[Project] = []
        cursor = None
        for _ in range(10):
            data = await self.client.call(
                "GET", "/v9/projects", query={"limit": 100, **({"until": cursor} if cursor else {})}
            )
            result.extend(Project(id=item["id"], name=item["name"]) for item in data["projects"])
            cursor = data.get("pagination", {}).get("next")
            if not cursor:
                return tuple(result)
        raise ConnectorError("host-limit", "Select an account with fewer than 1,000 projects")

    async def variables(self, project: str) -> tuple[Variable, ...]:
        data = await self.client.call(
            "GET", f"/v10/projects/{quote(project, safe='')}/env", query={"decrypt": "false"}
        )
        result = []
        for item in data["envs"]:
            if item.get("gitBranch") or item.get("customEnvironmentIds"):
                continue
            targets = item.get("target", [])
            if not isinstance(targets, list):
                targets = [targets]
            if "production" not in targets or item.get("type") == "system":
                continue
            result.append(
                Variable(
                    reference=f"vercel://{project}/production/{item['id']}",
                    project_id=project,
                    environment="production",
                    name=item["key"],
                    key_id=item["id"],
                    readable=item.get("type") in {"plain", "encrypted"},
                )
            )
        return tuple(result)

    async def read(self, variable: Variable) -> SecretValue | None:
        if not variable.readable:
            return None
        result = await self.client.call("GET", self._path(variable).replace("/v9/", "/v1/", 1))
        value = result.get("value")
        return SecretValue(value.encode()) if isinstance(value, str) else None

    async def write(self, variable: Variable, value: SecretValue) -> None:
        current = await self.client.call(
            "GET",
            f"/v10/projects/{quote(variable.project_id, safe='')}/env",
            query={"decrypt": "false"},
        )
        selected = next((item for item in current["envs"] if item["id"] == variable.key_id), None)
        if not selected or selected.get("key") != variable.name:
            raise ConnectorError("host-variable", "Selected variable changed")
        targets = selected.get("target", [])
        if not isinstance(targets, list):
            targets = [targets]
        if (
            variable.environment not in targets
            or selected.get("gitBranch")
            or selected.get("customEnvironmentIds")
            or selected.get("type") == "system"
        ):
            raise ConnectorError(
                "host-shared-variable",
                "Use a variable that includes the production environment before rotating it",
            )
        await self.client.call(
            "PATCH", self._path(variable), body={"value": value.bytes().decode()}
        )

    async def current(self, variable: Variable) -> Deployment:
        data = await self.client.call("GET", f"/v9/projects/{quote(variable.project_id, safe='')}")
        current = data.get("targets", {}).get(variable.environment)
        if not current:
            raise ConnectorError("host-deployment", "The environment has no active deployment")
        return _deployment(current)

    async def deploy(self, variable: Variable, previous: Deployment, request_id: str) -> Deployment:
        await self.status(variable, previous.id)
        data = await self.client.call("GET", f"/v9/projects/{quote(variable.project_id, safe='')}")
        response = await self.client.call(
            "POST",
            "/v13/deployments",
            body={
                "name": data["name"],
                "project": variable.project_id,
                "deploymentId": previous.id,
                "target": variable.environment,
                "meta": {"kinuRun": request_id},
            },
        )
        return _deployment(response)

    async def status(self, variable: Variable, identity: str) -> Deployment:
        data = await self.client.call("GET", f"/v13/deployments/{quote(identity, safe='')}")
        if data.get("projectId") != variable.project_id:
            raise ConnectorError("host-deployment", "Deployment belongs to another project")
        return _deployment(data)

    async def restore(self, variable: Variable, identity: str) -> Deployment:
        if variable.environment != "production":
            raise ConnectorError(
                "host-rollback", "Automatic rollback requires a production deployment"
            )
        await self.status(variable, identity)
        project = quote(variable.project_id, safe="")
        await self.client.call(
            "POST", f"/v1/projects/{project}/rollback/{quote(identity, safe='')}"
        )
        return await self.status(variable, identity)

    @staticmethod
    def _path(variable: Variable) -> str:
        project = quote(variable.project_id, safe="")
        return f"/v9/projects/{project}/env/{quote(variable.key_id, safe='')}"


def _deployment(data: dict[str, object]) -> Deployment:
    state = data.get("readyState", data.get("state", "QUEUED"))
    url = str(data["url"]) if data.get("url") else None
    progress: DeploymentProgress | None = (
        "ready"
        if state == "READY"
        else "failed"
        if state in {"ERROR", "CANCELED"}
        else "building"
        if state in {"BUILDING", "DEPLOYING"}
        else "queued"
        if state in {"QUEUED", "INITIALIZING"}
        else None
    )
    return Deployment(
        id=str(data["id"]),
        state="ready"
        if state == "READY"
        else "failed"
        if state in {"ERROR", "CANCELED"}
        else "pending",
        url="https://" + url if url else None,
        progress=progress,
        progress_label=str(state) if progress else None,
    )
