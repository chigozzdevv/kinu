import json
import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from browser.driver import BrowserDriver
from browser.launch import Tab
from browser.planner import Choice
from contracts import BrowserActionKind, BrowserPolicy, PlaybookDraft
from core.errors import ResourceConflictError
from playwright.async_api import Route, async_playwright
from tests.storage.hosting.rotation.fixture import NEW, Platform


def executable() -> str:
    selected = (
        os.environ.get("KINU_TEST_CHROME")
        or shutil.which("google-chrome")
        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    if not Path(selected).is_file():
        pytest.skip("A Chromium executable is required for real browser integration")
    return selected


class Planner:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.requests: list[tuple[str, str]] = []
        self.stop = False

    async def choose(
        self,
        org: str,
        objective: str,
        controls: dict[str, str],
        image: bytes,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> Choice:
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        self.calls.append(controls)
        self.requests.append((org, objective))
        assert NEW not in json.dumps(controls)
        if self.stop:
            raise ResourceConflictError("Agent did not select a control")
        if progress:
            await progress("The requested action")
            await progress("The requested action matches the approved control.")
        return Choice(
            control=self.pick(objective, controls),
            summary="The requested action matches the approved control.",
        )

    @staticmethod
    def pick(objective: str, controls: dict[str, str]) -> str:
        step = objective.split("Step: ", 1)[1].splitlines()[0]
        expected = {
            "open_create": "button: Create API key",
            "name_key": "input",
            "limit_permission": "combobox: Full access",
            "limit_domain": "combobox: All domains",
            "create_key": "button: Add",
            "old_menu": "button: More actions",
            "open_retirement": "menuitem: Delete API key",
            "confirm_old": "input",
            "retire_key": "button: Delete",
        }
        if step == "create_key" and "button: Create key" in controls.values():
            return next(key for key, value in controls.items() if value == "button: Create key")
        if step == "retire_key" and "button: Retire key" in controls.values():
            return next(key for key, value in controls.items() if value == "button: Retire key")
        options = [key for key, value in controls.items() if value.startswith(expected[step])]
        return options[0]


class Launcher:
    def __init__(self, platform: Platform) -> None:
        self.platform, self.closed = platform, 0

    @asynccontextmanager
    async def open(
        self,
        definition: PlaybookDraft,
        state: dict[str, Any] | None = None,
        extra_domains: tuple[str, ...] = (),
        pin: bool = True,
    ) -> AsyncIterator[Tab]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=executable(), args=["--no-sandbox"]
            )
            try:
                context = await browser.new_context(
                    storage_state=cast(Any, state), service_workers="block", accept_downloads=False
                )
                page = await context.new_page()
                driver = BrowserDriver(
                    page,
                    BrowserPolicy(
                        allowed_domains=definition.allowed_domains,
                        allowed_actions=frozenset(BrowserActionKind),
                        login_url_pattern=definition.login_url_pattern,
                    ),
                )
                await driver.enforce_egress()

                async def fixture(route: Route) -> None:
                    path = route.request.url.removeprefix("https://portal.example")
                    if path == "/created":
                        self.platform.keys["new"] = {"value": NEW, "active": True}
                        await route.fulfill(json={"ok": True})
                        return
                    if path.startswith("/retire/"):
                        key = path.rsplit("/", 1)[-1]
                        self.platform.keys[key]["active"] = False
                        self.platform.revoked.append(key)
                        await route.fulfill(json={"ok": True})
                        return
                    if path.startswith("/keys/"):
                        key = path.rsplit("/", 1)[-1]
                        html = f"""<h1>API keys</h1><button id="retire"
onclick="fetch('/retire/{key}',{{method:'POST'}})">Retire key</button>"""
                    else:
                        ids = "".join(
                            f'<a class="key-id" href="/keys/{key}">{key}</a>'
                            for key, item in self.platform.keys.items()
                            if item["active"]
                        )
                        html = f"""<h1>API keys</h1>{ids}<input id="login" type="password">
<button id="create" onclick="createKey()">Create key</button>
<script>document.cookie='portal_session=private-browser-cookie; Secure; SameSite=Lax';
async function createKey() {{ await fetch('/created',{{method:'POST'}});
window.originalKey={json.dumps(NEW)};
document.body.insertAdjacentHTML('beforeend',
  '<input id="key"><a id="identity" href="/keys/new">new</a>');
document.querySelector('#key').value=window.originalKey; }}</script>"""
                    await route.fulfill(content_type="text/html", body=html)

                await page.route("**/*", fixture)
                yield Tab(page, driver, context)
            finally:
                await browser.close()
                self.closed += 1


def response(platform: Platform, request: httpx.Request) -> httpx.Response:
    result = platform.response(request)
    if request.url.host == "issuer.example" and request.url.path == "/scopes" and result.is_success:
        return httpx.Response(200, json=result.json() | {"scopes": ["read"]})
    return result
