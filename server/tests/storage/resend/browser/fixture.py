import json
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html import escape
from typing import Any, cast

from browser.driver import BrowserDriver
from browser.launch import Tab
from contracts import BrowserActionKind, BrowserPolicy, PlaybookDraft
from playwright.async_api import Route, async_playwright
from tests.storage.browser.fixture import executable
from tests.storage.resend.fixture import Platform


class Launcher:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.failures: list[str] = []

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
                    storage_state=cast(Any, state), service_workers="block"
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
                await page.route("**/*", self.route)
                try:
                    yield Tab(page, driver, context)
                except Exception:
                    self.failures.append(traceback.format_exc())
                    raise
            finally:
                await browser.close()

    async def route(self, route: Route) -> None:
        path = route.request.url.removeprefix("https://resend.com")
        if path == "/created":
            body = route.request.post_data_json
            assert isinstance(body, dict)
            assert body["permission"] == "Sending access" and body["domain"] == "issuer.example"
            identity = "0b0ca8e1-fd3f-4edf-b762-000000009999"
            assert identity not in self.platform.keys
            item = {
                "id": identity,
                "name": body["name"],
                "token": "re_private-browser-fixture-" + identity,
            }
            self.platform.keys[identity] = item
            self.platform.created.append(identity)
            await route.fulfill(json={**item, "row": self.row(item)})
            return
        if path.startswith("/retire/"):
            identity = path.rsplit("/", 1)[1]
            item = self.platform.keys[identity]
            body = route.request.post_data_json
            assert isinstance(body, dict) and body["name"] == item["name"]
            self.platform.keys.pop(identity)
            self.platform.revoked.append(identity)
            await route.fulfill(json={"ok": True})
            return
        if path.startswith("/api-keys/"):
            identity = path.rsplit("/", 1)[1]
            name = self.platform.keys[identity]["name"]
            html = f"<h1>{escape(name)}</h1>" + _RETIRE.replace("FIXTURE_ID", identity).replace(
                "FIXTURE_NAME", json.dumps(name)
            )
        else:
            html = (
                '<h1>API keys</h1><table id="keys"><tbody>'
                + "".join(self.row(item) for item in self.platform.keys.values())
                + "</tbody></table>"
                + _CREATE
            )
        await route.fulfill(content_type="text/html; charset=utf-8", body=html)

    @staticmethod
    def row(item: dict[str, str]) -> str:
        href = "/api-keys/" + item["id"]
        return (
            f'<tr><td><a href="{href}"><span></span></a>'
            f'<a href="{href}"><span>{escape(item["name"])}</span></a></td></tr>'
        )


_CREATE = """<button onclick="openCreate()">Create API key</button><script>
document.cookie='session=private-fixture-cookie; Secure;SameSite=Lax';
function openCreate() {
  const dialog = document.createElement('div'); dialog.setAttribute('role', 'dialog');
  dialog.innerHTML = `<label for="name">Name</label><input id="name">
  <label for="permission">Permission</label>
  <button id="permission-button" role="combobox" onclick="choosePermission()">Full access</button>
  <input type="hidden" value="full_access">
  <label for="domain">Domain</label>
  <button id="domain-button" role="combobox" onclick="chooseDomain()" disabled>All domains</button>
  <button type="submit" onclick="createKey()">Add<kbd>Ctrl ↩︎</kbd></button>`;
  document.body.appendChild(dialog);
}
function choosePermission() {
  const list = document.createElement('div'); list.setAttribute('role', 'listbox');
  list.innerHTML = `<button role="option" onclick="
    document.querySelector('#permission-button').textContent='Sending access';
    document.querySelector('#domain-button').disabled=false;
    this.parentNode.remove()">Sending access</button>`;
  document.body.appendChild(list);
}
function chooseDomain() {
  const list = document.createElement('div'); list.setAttribute('role', 'listbox');
  list.innerHTML = `<button role="option" onclick="
    document.querySelector('#domain-button').textContent='issuer.example';this.parentNode.remove()">issuer.example</button>`;
  document.body.appendChild(list);
}
async function createKey() {
  const data = await (await fetch('/created', {
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:document.querySelector('#name').value,
      permission:document.querySelector('#permission-button').textContent,
      domain:document.querySelector('#domain-button').textContent})})).json();
  document.querySelector('#keys tbody').insertAdjacentHTML('beforeend',data.row);
  document.querySelector('table').setAttribute('aria-hidden','true');
  document.querySelector('[role=dialog]').innerHTML='<input id="token">';
  document.querySelector('#token').setAttribute('value',data.token);
}
</script>"""

_RETIRE = """<button onclick="menu()">More actions</button><script>
function menu() {
  const menu = document.createElement('div'); menu.setAttribute('role','menu');
  menu.innerHTML='<button role="menuitem" onclick="retire()">Delete API key</button>';
  document.body.appendChild(menu);
}
function retire() {
  document.querySelector('[role=menu]').remove();
  const dialog=document.createElement('div');dialog.setAttribute('role','dialog');
  dialog.innerHTML='<input placeholder="Enter API Key name">'+
    '<button type="submit" onclick="confirmOld()">Delete API Key<kbd>Ctrl ↩︎</kbd></button>';
  document.body.appendChild(dialog);
}
async function confirmOld() {
  const name=document.querySelector('input').value;
  if(name!==FIXTURE_NAME) throw new Error('Wrong key');
  await fetch('/retire/FIXTURE_ID',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name})});
  location.assign('/api-keys');
}
</script>"""
