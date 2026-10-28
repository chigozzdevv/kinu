import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from contracts import Application, ConsumerService, Environment
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError
from core.inventory.store import InventoryStore
from core.storage.database import Database
from executor.vault import Vault
from hosting.grants import digest
from hosting.models import Begin, Selection
from hosting.service import Service
from pydantic import SecretStr

pytestmark = pytest.mark.anyio
SESSION = "a" * 64
TOKEN = "host-token-fixture-private"


async def workspace(database: Database, identity: AuthenticatedIdentity) -> str:
    accounts = AccountStore(database, lambda: datetime.now(UTC))
    return (
        await AccountService(accounts, lambda: datetime.now(UTC)).create_organisation(
            identity, "Hosting"
        )
    ).organisation.id


def response(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v2/oauth/access_token":
        body = parse_qs(request.content.decode())
        assert body["client_id"] == ["app-one"] and body["client_secret"] == ["app-secret-private"]
        assert body["redirect_uri"] == ["http://localhost:8080/connections/vercel/callback"]
        return httpx.Response(
            200,
            json={"access_token": TOKEN, "team_id": "team-one", "configuration_id": "installation"},
        )
    assert request.headers["authorization"] == "Bearer " + TOKEN
    if request.url.path == "/v9/projects":
        assert request.url.params["teamId"] == "team-one"
        return httpx.Response(
            200,
            json={
                "projects": [
                    {"id": "project-one", "name": "App"},
                    {"id": "project-two", "name": "Other"},
                ]
            },
        )
    if request.url.path == "/v10/projects/project-one/env":
        return httpx.Response(
            200,
            json={
                "envs": [
                    {
                        "id": "key-one",
                        "key": "API_KEY",
                        "type": "sensitive",
                        "target": ["production"],
                        "value": "workload-fixture-private",
                    }
                ]
            },
        )
    raise AssertionError(str(request.url))


async def connect(service: Service, org: str) -> str:
    begin = await service.begin(
        org, "actor_one", SESSION, Begin(platform="vercel", display_name="Vercel")
    )
    state = parse_qs(urlsplit(begin["authorization_url"]).query)["state"][0]
    return await service.finish(org, "vercel", state, "actor_one", SESSION, "code")


async def test_oauth_is_single_use_session_bound_and_project_selection_limits_metadata(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    vault = Vault(database, SecretStr("private-hosting-key-material-at-least-32"))
    await vault.initialize()
    await vault.configure(
        "vercel", {"slug": "kinu", "client_id": "app-one", "client_secret": "app-secret-private"}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        service = Service(database, vault, http, "http://localhost:8080")
        await service.grants.initialize()
        begin = await service.begin(
            org, "actor_one", SESSION, Begin(platform="vercel", display_name="Vercel")
        )
        state = parse_qs(urlsplit(begin["authorization_url"]).query)["state"][0]
        with pytest.raises(ResourceConflictError):
            await service.owner("vercel", state, "actor_other", SESSION)
        with pytest.raises(ResourceConflictError):
            await service.owner("railway", state, "actor_one", SESSION)
        with pytest.raises(ResourceConflictError):
            await service.finish("org_other", "vercel", state, "actor_one", SESSION, "code")
        assert await service.owner("vercel", state, "actor_one", SESSION) == org
        connection_id = await service.finish(org, "vercel", state, "actor_one", SESSION, "code")
        with pytest.raises(ResourceConflictError):
            await service.finish(org, "vercel", state, "actor_one", SESSION, "code")
        stored = await database.collection("host_grants").find_one({"id": connection_id})
        assert TOKEN not in str(stored)
        with pytest.raises(ResourceConflictError):
            await service.projects(org, connection_id, "actor_other", SESSION)
        projects = await service.projects(org, connection_id, "actor_one", SESSION)
        assert TOKEN not in json.dumps(projects)
        connection = await service.save(
            org, connection_id, "actor_one", SESSION, Selection(project_ids=("project-one",))
        )
        assert connection.status == "ready" and connection.allowed_resources == (
            "vercel://project-one",
        )
        values = await service.variables(org, connection_id, "project-one")
        assert len(values) == 1 and "workload-fixture-private" not in str(values)
        with pytest.raises(ResourceConflictError):
            await service.variables(org, connection_id, "project-two")
        now = datetime.now(UTC)
        app = Application(
            id="app_one", organisation_id=org, display_name="App", created_at=now, updated_at=now
        )
        environment = Environment(
            id="env_one",
            organisation_id=org,
            application_id=app.id,
            display_name="Production",
            production=True,
            region="global",
            created_at=now,
            updated_at=now,
        )
        consumer = ConsumerService(
            id="service_one",
            organisation_id=org,
            application_id=app.id,
            environment_id=environment.id,
            runtime_connection_id=connection.id,
            runtime_resource="vercel://project-one/production",
            display_name="App",
            created_at=now,
            updated_at=now,
        )
        await InventoryStore(database).add_application_setup(app, environment, consumer)
        with pytest.raises(ResourceConflictError):
            await service.save(
                org,
                connection_id,
                "actor_one",
                SESSION,
                Selection(expected_revision=connection.revision, project_ids=("project-two",)),
            )
        assert (
            await InventoryStore(database).get_connection(org, connection_id)
        ).allowed_resources == ("vercel://project-one",)


async def test_pending_consent_expires_and_railway_refresh_is_serialized(
    database: Database,
) -> None:
    from hosting.grants import Grants
    from hosting.oauth import OAuth

    vault = Vault(database, SecretStr("private-hosting-key-material-at-least-32"))
    grants = Grants(vault)
    await grants.initialize()
    state = "s" * 43
    await grants.begin(state, "org_one", "actor_one", SESSION, {"platform": "railway"})
    await database.collection("host_pending").update_one(
        {"state": digest(state)}, {"$set": {"expires_on": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    with pytest.raises(ResourceConflictError):
        await grants.owner(state, "actor_one", SESSION, "railway")
    await grants.put(
        "org_one",
        "connection_one",
        {
            "platform": "railway",
            "access_token": "expired",
            "refresh_token": "refresh-private",
            "expires_at": 0,
        },
    )
    calls = []

    async def refresh(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        await asyncio.sleep(0.05)
        assert request.headers["authorization"].startswith("Basic ")
        assert parse_qs(request.content.decode())["refresh_token"] == ["refresh-private"]
        return httpx.Response(
            200,
            json={"access_token": TOKEN, "refresh_token": "rotated-private", "expires_in": 3600},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as http:
        oauth = OAuth(
            http,
            "railway",
            {"client_id": "app", "client_secret": "private"},
            "http://localhost:8080",
        )
        result = await asyncio.gather(
            *(grants.access("org_one", "connection_one", oauth) for _ in range(4))
        )
        assert len(calls) == 1 and all(item["access_token"] == TOKEN for item in result)
        assert (await grants.get("org_one", "connection_one"))[1][
            "refresh_token"
        ] == "rotated-private"
        assert "rotated-private" not in str(
            await database.collection("host_grants").find_one({"id": "connection_one"})
        )


async def test_public_host_routes_enforce_workspace_csrf_and_keep_tokens_out_of_responses(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    from api.app import create_app
    from api.deps import ApiServices
    from core.auth import AccessControl
    from core.private import PrivateClient
    from executor.app import app as executor
    from executor.config import ExecutorSettings
    from executor.runtime import Runtime
    from jobs.queue import Jobs
    from tests.browser import HEADERS, make_browser

    org = await workspace(database, identity)
    vault = Vault(database, SecretStr("private-hosting-key-material-at-least-32"))
    await vault.initialize()
    await vault.configure(
        "vercel", {"slug": "kinu", "client_id": "app-one", "client_secret": "app-secret-private"}
    )
    queue = Jobs("redis://localhost:6379")
    config = ExecutorSettings(
        vault_key=SecretStr("private-hosting-key-material-at-least-32"),
        executor_token=SecretStr("private-token"),
        native_token=SecretStr("native-token"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as external:
        runtime = Runtime(config, database, vault, external, queue)
        runtime.hosts.origin = "http://localhost:8080"
        await runtime.hosts.grants.initialize()
        executor.state.service = runtime
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=executor), base_url="http://executor"
        ) as internal:
            public = create_app(
                ApiServices(
                    access=AccessControl(AccountStore(database, lambda: datetime.now(UTC))),
                    private=PrivateClient(internal, "http://executor", SecretStr("private-token")),
                ),
                browser=make_browser(identity),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=public), base_url="http://test"
            ) as http:
                path = f"/v1/organisations/{org}/connections/hosting"
                body = {"platform": "vercel", "display_name": "Vercel"}
                assert (await http.post(path, json=body)).status_code == 401
                assert (
                    await http.post(path, headers={"Cookie": HEADERS["Cookie"]}, json=body)
                ).status_code == 403
                assert (
                    await http.post(path.replace(org, "org_other"), headers=HEADERS, json=body)
                ).status_code == 403
                begin = await http.post(path, headers=HEADERS, json=body)
                assert begin.status_code == 200, begin.text
                state = parse_qs(urlsplit(begin.json()["authorization_url"]).query)["state"][0]
                callback = await http.get(
                    "/connections/vercel/callback",
                    headers=HEADERS,
                    params={"state": state, "code": "code"},
                )
                assert callback.status_code == 303
                parameters = parse_qs(urlsplit(callback.headers["location"]).query)
                connection_id = parameters["hosting"][0]
                assert connection_id.startswith("connection_")
                saved = await http.put(
                    path + "/" + connection_id,
                    headers=HEADERS,
                    json={"project_ids": ["project-one"]},
                )
                assert saved.status_code == 200, saved.text
                assert TOKEN not in saved.text and "app-secret-private" not in saved.text
                variables = await http.get(
                    path + "/" + connection_id + "/variables",
                    headers=HEADERS,
                    params={"project_id": "project-one"},
                )
                assert (
                    variables.status_code == 200
                    and "workload-fixture-private" not in variables.text
                )
                assert (
                    await http.get(
                        path + "/" + connection_id + "/variables",
                        headers=HEADERS,
                        params={"project_id": "project-two"},
                    )
                ).status_code == 409
    await queue.close()
