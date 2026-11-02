import json

import httpx
import pytest
from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.hosting.client import Client
from connectors.hosting.models import Deployment, Variable
from connectors.hosting.render import Render
from connectors.hosting.vercel import Vercel

pytestmark = pytest.mark.anyio
SECRET = "private-fixture-key-never-returned-in-metadata"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def variable(platform: str = "vercel") -> Variable:
    return Variable(
        reference=f"{platform}://project/production/key",
        project_id="project",
        environment="production",
        name="API_KEY",
        key_id="key",
    )


async def test_vercel_discovers_metadata_and_keeps_secret_values_private() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == "Bearer management-token"
        if request.url.path == "/v9/projects":
            return httpx.Response(200, json={"projects": [{"id": "project", "name": "App"}]})
        assert request.url.path == "/v10/projects/project/env"
        assert request.url.params["decrypt"] == "false"
        return httpx.Response(
            200,
            json={
                "envs": [
                    {
                        "id": "key",
                        "key": "API_KEY",
                        "type": "sensitive",
                        "target": ["production"],
                        "value": SECRET,
                    },
                    {
                        "id": "branch",
                        "key": "BRANCH_KEY",
                        "type": "encrypted",
                        "target": ["preview"],
                        "gitBranch": "feature",
                    },
                    {
                        "id": "system",
                        "key": "VERCEL_URL",
                        "type": "system",
                        "target": ["production"],
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        host = Vercel(
            Client(http, "management-token", "https://api.vercel.com", {"teamId": "team"})
        )
        assert (await host.projects())[0].name == "App"
        selected = await host.variables("project")
        assert len(selected) == 1 and not selected[0].readable
        assert selected[0].environment_label == "production"
        assert SECRET not in selected[0].model_dump_json()
        assert await host.read(selected[0]) is None
        assert len(calls) == 2
        assert all(request.url.params["teamId"] == "team" for request in calls)


@pytest.mark.parametrize("targets", [["production"], ["production", "preview"]])
async def test_vercel_updates_only_one_selected_variable_and_rejects_shared_targets(
    targets: list[str],
) -> None:
    writes = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"envs": [{"id": "key", "key": "API_KEY", "target": targets}]}
            )
        assert request.method == "PATCH" and request.url.path == "/v9/projects/project/env/key"
        writes.append(json.loads(request.content))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        host = Vercel(Client(http, "token", "https://api.vercel.com"))
        if len(targets) > 1:
            with pytest.raises(ConnectorError, match="one environment"):
                await host.write(variable(), SecretValue(SECRET.encode()))
            assert not writes
        else:
            await host.write(variable(), SecretValue(SECRET.encode()))
            assert writes == [{"value": SECRET}]


async def test_vercel_deploys_the_previous_source_and_checks_project_before_rollback() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/v9/projects/project":
            return httpx.Response(
                200,
                json={
                    "name": "App",
                    "targets": {"production": {"id": "old", "readyState": "READY"}},
                },
            )
        if path == "/v13/deployments":
            body = json.loads(request.content)
            assert body["deploymentId"] == "old" and body["project"] == "project"
            assert body["meta"] == {"kinuRun": "run_one"}
            assert not body.get("withLatestCommit")
            return httpx.Response(200, json={"id": "new", "readyState": "BUILDING"})
        if path == "/v13/deployments/old":
            return httpx.Response(
                200, json={"id": "old", "projectId": "project", "readyState": "READY"}
            )
        if path == "/v13/deployments/foreign":
            return httpx.Response(
                200, json={"id": "foreign", "projectId": "other", "readyState": "READY"}
            )
        assert path == "/v1/projects/project/rollback/old"
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        host = Vercel(Client(http, "token", "https://api.vercel.com"))
        previous = await host.current(variable())
        assert (await host.deploy(variable(), previous, "run_one")).state == "pending"
        with pytest.raises(ConnectorError, match="another project"):
            await host.status(variable(), "foreign")
        with pytest.raises(ConnectorError, match="another project"):
            await host.restore(variable(), "foreign")
        with pytest.raises(ConnectorError, match="production"):
            await host.restore(variable().model_copy(update={"environment": "preview"}), "old")
        assert len(calls) == 6


async def test_render_paginates_names_without_returning_values_and_updates_only_selected_key() -> (
    None
):
    writes = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            assert request.url.path == "/v1/services/project/env-vars/key"
            writes.append(json.loads(request.content))
            return httpx.Response(200, json={"key": "key", "value": SECRET})
        if request.url.params.get("cursor"):
            assert request.url.params["cursor"] == "cursor99"
            return httpx.Response(
                200, json=[{"envVar": {"key": "LAST", "value": SECRET}, "cursor": "last"}]
            )
        return httpx.Response(
            200,
            json=[
                {"envVar": {"key": f"KEY_{i}", "value": SECRET}, "cursor": f"cursor{i}"}
                for i in range(100)
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        host = Render(Client(http, "token", "https://api.render.com"))
        metadata = await host.variables("project")
        assert len(metadata) == 101
        assert metadata[0].reference == "render://project/production/KEY_0"
        assert metadata[0].environment_label is None
        assert SECRET not in str(metadata)
        await host.write(variable("render"), SecretValue(SECRET.encode()))
        assert writes == [{"value": SECRET}]


async def test_render_redeploys_existing_commit_and_rolls_back_selected_deployment() -> None:
    writes = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"id": "old", "status": "live", "commit": {"id": "old-source"}}
            )
        writes.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": "new", "status": "created"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        host = Render(Client(http, "token", "https://api.render.com"))
        result = await host.deploy(
            variable("render"), Deployment(id="old", state="ready"), "run_one"
        )
        assert result.state == "pending"
        await host.restore(variable("render"), "old")
        assert writes == [
            ("/v1/services/project/deploys", {"commitId": "old-source"}),
            ("/v1/services/project/rollback", {"deployId": "old"}),
        ]


async def test_host_client_rejects_redirects_untrusted_origins_and_secret_error_bodies() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://other.example"}, text=SECRET)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ConnectorError, match="Unsupported"):
            Client(http, "token", "https://other.example")
        client = Client(http, "token", "https://api.render.com")
        with pytest.raises(ConnectorError) as error:
            await client.call("GET", "/v1/services")
        assert SECRET not in str(error.value)
        assert len(calls) == 1
        with pytest.raises(ConnectorError, match="Invalid"):
            await client.call("GET", "//other.example")
