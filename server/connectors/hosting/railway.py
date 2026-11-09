from typing import Any
from urllib.parse import quote

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.hosting.client import Client
from connectors.hosting.models import Deployment, DeploymentProgress, Project, Variable


class Railway:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def projects(self) -> tuple[Project, ...]:
        data = await self.client.graphql(
            """query {
          externalWorkspaces {
            id
            name
            projects { id name }
          }
        }""",
            {},
        )
        result: list[Project] = []
        seen: set[str] = set()
        for workspace in data["externalWorkspaces"]:
            for item in workspace["projects"]:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                result.append(Project(id=item["id"], name=item["name"]))
        return tuple(result)

    async def variables(self, project: str) -> tuple[Variable, ...]:
        data = await self.client.graphql(
            """query($id: String!) {
          project(id: $id) { id services { edges { node { id name } } }
            environments { edges { node { id name } } } }
        }""",
            {"id": project},
        )
        selected = data["project"]
        if selected["id"] != project:
            raise ConnectorError("host-project", "Hosting API returned another project")
        result = []
        services, environments = selected["services"]["edges"], selected["environments"]["edges"]
        if len(services) * len(environments) > 100:
            raise ConnectorError("host-limit", "Project has too many service environments")
        for environment in environments:
            for service in services:
                values = await self._values(
                    project, environment["node"]["id"], service["node"]["id"], unrendered=True
                )
                for name, value in values.items():
                    if not isinstance(value, str) or "${{" in value or name.startswith("RAILWAY_"):
                        continue
                    result.append(
                        Variable(
                            reference=(
                                f"railway://{project}/{environment['node']['id']}/"
                                f"{service['node']['id']}/{quote(name, safe='')}"
                            ),
                            project_id=project,
                            environment=environment["node"]["id"],
                            service_id=service["node"]["id"],
                            key_id=name,
                            name=name,
                            readable=True,
                            environment_name=environment["node"]["name"],
                            service_name=service["node"]["name"],
                        )
                    )
        return tuple(result)

    async def _values(
        self, project: str, environment: str, service: str | None, *, unrendered: bool = False
    ) -> dict[str, Any]:
        data = await self.client.graphql(
            """query($project: String!, $environment: String!,
          $service: String, $unrendered: Boolean) {
          variables(projectId: $project, environmentId: $environment,
            serviceId: $service, unrendered: $unrendered)
        }""",
            {
                "project": project,
                "environment": environment,
                "service": service,
                "unrendered": unrendered,
            },
        )
        values: dict[str, Any] = data["variables"]
        return values

    async def read(self, variable: Variable) -> SecretValue | None:
        values = await self._values(variable.project_id, variable.environment, variable.service_id)
        value = values.get(variable.name)
        return SecretValue(value.encode()) if isinstance(value, str) else None

    async def write(self, variable: Variable, value: SecretValue) -> None:
        values = await self._values(
            variable.project_id, variable.environment, variable.service_id, unrendered=True
        )
        if variable.name not in values or "${{" in str(values[variable.name]):
            raise ConnectorError(
                "host-variable", "Selected variable changed or is a shared reference"
            )
        await self.client.graphql(
            """mutation($input: VariableUpsertInput!) {
          variableUpsert(input: $input)
        }""",
            {
                "input": {
                    "projectId": variable.project_id,
                    "environmentId": variable.environment,
                    "serviceId": variable.service_id,
                    "name": variable.name,
                    "value": value.bytes().decode(),
                    "skipDeploys": True,
                }
            },
        )

    async def _deployments(self, variable: Variable) -> list[dict[str, Any]]:
        data = await self.client.graphql(
            """query($input: DeploymentListInput!) {
          deployments(input: $input, first: 100) { edges { node { id status url } } }
        }""",
            {
                "input": {
                    "projectId": variable.project_id,
                    "environmentId": variable.environment,
                    "serviceId": variable.service_id,
                }
            },
        )
        return [item["node"] for item in data["deployments"]["edges"]]

    async def current(self, variable: Variable) -> Deployment:
        values = await self._deployments(variable)
        selected = next((item for item in values if item["status"] == "SUCCESS"), None)
        if not selected:
            raise ConnectorError("host-deployment", "Service has no active deployment")
        return _deployment(selected)

    async def status(self, variable: Variable, identity: str) -> Deployment:
        selected = next(
            (item for item in await self._deployments(variable) if item["id"] == identity), None
        )
        if not selected:
            raise ConnectorError(
                "host-deployment", "Deployment is outside this service environment"
            )
        return _deployment(selected)

    async def deploy(self, variable: Variable, previous: Deployment, request_id: str) -> Deployment:
        await self.status(variable, previous.id)
        data = await self.client.graphql(
            """mutation($id: String!) {
          deploymentRedeploy(id: $id) { id status }
        }""",
            {"id": previous.id},
        )
        return _deployment(data["deploymentRedeploy"])

    async def restore(self, variable: Variable, identity: str) -> Deployment:
        await self.status(variable, identity)
        data = await self.client.graphql(
            """mutation($id: String!) {
          deploymentRollback(id: $id) { id status }
        }""",
            {"id": identity},
        )
        return _deployment(data["deploymentRollback"])


def _deployment(data: dict[str, Any]) -> Deployment:
    status = data["status"]
    progress: DeploymentProgress | None = (
        "ready"
        if status == "SUCCESS"
        else "failed"
        if status in {"FAILED", "CRASHED", "REMOVED", "SKIPPED"}
        else "building"
        if status in {"BUILDING", "DEPLOYING"}
        else "queued"
        if status in {"INITIALIZING", "WAITING", "QUEUED"}
        else None
    )
    return Deployment(
        id=data["id"],
        state="ready"
        if status == "SUCCESS"
        else "failed"
        if status in {"FAILED", "CRASHED", "REMOVED", "SKIPPED"}
        else "pending",
        url=data.get("url"),
        progress=progress,
        progress_label=str(status) if progress else None,
    )
