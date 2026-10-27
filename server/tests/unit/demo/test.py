from typing import Any

import httpx
import pytest
from connectors.aws import Role
from connectors.aws.client import AwsError
from demo.app import create_app
from demo.auth import identity, session, signature
from demo.store import ProviderStore

pytestmark = pytest.mark.anyio
SEED = b"test-provider-seed"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Aws:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    async def call(
        self,
        service: str,
        operation: str,
        region: str,
        *,
        role: Role | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        if service == "secretsmanager":
            return {"SecretString": SEED.decode()}
        item = parameters.get("Item") or parameters.get("Key") or {}
        key = (item.get("org", {}).get("S", ""), item.get("id", {}).get("S", ""))
        if operation == "put_item":
            if key in self.items:
                raise AwsError(service, operation, "ConditionalCheckFailedException")
            self.items[key] = item
        elif operation == "get_item":
            return {"Item": self.items.get(key)}
        elif operation == "update_item":
            values = parameters["ExpressionAttributeValues"]
            if ":state" in values:
                self.items[key]["state"] = values[":state"]
        elif operation == "query":
            org = parameters["ExpressionAttributeValues"][":org"]["S"]
            return {"Items": [item for (tenant, _), item in self.items.items() if tenant == org]}
        return {}


async def test_demo_issues_real_idempotent_credentials_and_retires_only_selected_tenant() -> None:
    aws = Aws()
    store = ProviderStore(aws, "eu-west-2", "demo", SEED)
    key_id, token = await store.create("org_one", "run_one")
    assert (key_id, token) == await store.create("org_one", "run_one")
    assert token not in str(aws.items)
    assert (await store.check("org_one", token)) is not None
    assert await store.check("org_two", token) is None
    assert await store.check("org_one", token + "changed") is None
    _, other = await store.create("org_two", "run_one")
    await store.revoke("org_one", key_id)
    assert await store.check("org_one", token) is None
    assert await store.check("org_two", other) is not None
    with pytest.raises(ValueError, match="retired"):
        await store.create("org_one", "run_one")


async def test_browser_admin_requires_origin_session_and_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KINU_DEMO_ORIGIN", "https://provider.example")
    aws = Aws()
    app = create_app(aws)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://provider.example"
    ) as http:
        assert (await http.get("/api/keys")).status_code == 401
        denied = await http.post(
            "/login",
            data={"organisation_id": "org_one", "password": signature(SEED, "admin", "org_one")},
            headers={"Origin": "https://attacker.example"},
        )
        assert denied.status_code == 403
        logged_in = await http.post(
            "/login",
            data={"organisation_id": "org_one", "password": signature(SEED, "admin", "org_one")},
            headers={"Origin": "https://provider.example"},
        )
        assert logged_in.status_code == 303
        assert "HttpOnly" in logged_in.headers["set-cookie"]
        assert (
            await http.post(
                "/keys",
                data={"request_id": "run_one"},
                headers={"Origin": "https://provider.example"},
            )
        ).status_code == 403
        assert not aws.items
        assert SEED.decode() not in (await http.get("/")).text
    cookie = session(SEED, "org_one")
    assert identity(SEED, cookie) == "org_one"
    assert identity(SEED, cookie + "changed") is None
