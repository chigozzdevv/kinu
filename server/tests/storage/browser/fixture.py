import json
import os
import shutil
from collections.abc import AsyncIterator
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
        self.stop = False

    async def choose(self, org: str, objective: str, controls: dict[str, str]) -> Choice:
        self.calls.append(controls)
        assert NEW not in json.dumps(controls)
        if self.stop:
            raise ResourceConflictError("Agent did not select a control")
        return Choice(
            control=next(iter(controls)),
            summary="The requested action matches the approved control.",
        )


class Launcher:
    def __init__(self, platform: Platform) -> None:
        self.platform, self.closed = platform, 0

    @asynccontextmanager
    async def open(
        self,
        definition: PlaybookDraft,
        state: dict[str, Any] | None = None,
        extra_domains: tuple[str, ...] = (),
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
