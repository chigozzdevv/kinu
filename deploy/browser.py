import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from browser.driver import BrowserDriver
from browser.planner import Planner
from capture import SecureCapture
from connectors.base import SecretValue
from contracts import (
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    PageCheckpoint,
    SecureField,
    Selector,
    SelectorKind,
)
from core.errors import ResourceConflictError
from playwright.async_api import Route, async_playwright


class Sink:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.value = b""

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        assert organisation_id == self.connection.organisation_id
        assert resource_id == self.connection.id
        return self.connection

    async def add_version_for(
        self,
        connection: Connection,
        secret: str,
        value: SecretValue,
        access_token: SecretValue | None = None,
    ) -> dict[str, object]:
        assert connection == self.connection
        self.value = value.bytes()
        return {"secret_reference": secret + "/versions/test"}

    async def disable_for(
        self,
        connection: Connection,
        version: str,
        access_token: SecretValue | None = None,
    ) -> dict[str, object]:
        raise AssertionError("Capture unexpectedly required compensation")


class Agent:
    def __init__(self, secret: str) -> None:
        self.secret, self.calls = secret, 0

    async def navigate(
        self,
        organisation_id: str,
        value: dict[str, Any],
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        assert organisation_id == "org_check" and self.secret not in str(value)
        self.calls += 1
        if "control" in value["schema"].get("properties", {}):
            assert "create_key" in str(value["messages"])
            assert value["schema"]["properties"]["control"]["enum"] == ["create_key", "stop"]
            return {
                "control": "create_key",
                "summary": "The approved control matches key creation.",
            }
        return {"progress": "The approved control was found.", "completed": True}


async def check() -> None:
    now = datetime.now(UTC)
    policy = BrowserPolicy(
        allowed_domains=("provider.invalid",), allowed_actions=frozenset({BrowserActionKind.CLICK})
    )
    session = BrowserSession(
        id="browser_check",
        organisation_id="org_check",
        run_id="run_check",
        playbook_id="playbook_check",
        playbook_version="version_check",
        provider_connection_id="provider_check",
        secret_store_connection_id="store_check",
        secret_resource="private://capture-check",
        status=BrowserStatus.CAPTURING,
        policy=policy,
        fencing_token=1,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    connection = Connection(
        id="store_check",
        organisation_id="org_check",
        platform="kinu",
        display_name="Capture check",
        roles=frozenset({ConnectionRole.SECRET_STORE}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
        authorization_reference="private://store_check",
        capabilities=frozenset({"secretStore.getVersion"}),
        allowed_resources=(session.secret_resource,),
        status=ConnectionStatus.READY,
        region="global",
        created_at=now,
        updated_at=now,
    )
    sink, secret = Sink(connection), "fake_" + secrets.token_hex(16)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=playwright.chromium.executable_path,
            args=["--no-sandbox"],
        )
        try:
            context = await browser.new_context(accept_downloads=False, service_workers="block")
            page = await context.new_page()

            async def fixture(route: Route) -> None:
                assert route.request.url == "https://provider.invalid/keys"
                await route.fulfill(
                    content_type="text/html",
                    body=f"""
<button onclick="window.original='{secret}';
 document.querySelector('#key').value=window.original">Create</button>
<input id="key"><span id="identity">key-check</span>
""",
                )

            await page.route("**/*", fixture)
            await page.goto("https://provider.invalid/keys")
            driver = BrowserDriver(page, policy)
            for observe in (driver.screenshot, driver.live_screenshot):
                try:
                    await observe(session)
                    raise AssertionError("Capture allowed a screenshot")
                except ResourceConflictError:
                    pass
            await page.get_by_role("button", name="Create").click()
            capture = SecureCapture(page, driver, sink, sink, lambda: datetime.now(UTC))
            result = await capture.transfer(
                "capture_check",
                session.organisation_id,
                session.id,
                SecureField(
                    name="key",
                    selector=Selector(kind=SelectorKind.CSS, value="#key"),
                    provider_id_selector=Selector(kind=SelectorKind.CSS, value="#identity"),
                ),
                PageCheckpoint(url_pattern="https://provider.invalid/keys"),
                connection.id,
                session.secret_resource,
            )
            assert sink.value.decode() == secret
            assert secret not in result.model_dump_json()
            assert await page.locator("#key").input_value() == "••••••••"
            assert secret in await page.content()
            await context.close()
            clean = await browser.new_context(accept_downloads=False, service_workers="block")
            page = await clean.new_page()
            assert secret not in await page.content()
            planner_image = await page.screenshot()
            await clean.close()
            sink.value = b""
        finally:
            await browser.close()
    agent = Agent(secret)
    choice = await Planner(agent).choose(
        "org_check", "Create a replacement key", {"create_key": "Create key"}, planner_image
    )
    assert choice.control == "create_key" and choice.summary and 0 < agent.calls <= 4
    print("Browser checks passed: private capture, context isolation and control selection.")


if __name__ == "__main__":
    asyncio.run(check())
