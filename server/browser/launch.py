import asyncio
import ipaddress
import os
import socket
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlsplit

from contracts import BrowserActionKind, BrowserPolicy, PlaybookDraft
from core.errors import ResourceConflictError
from playwright.async_api import BrowserContext, Page, async_playwright

from browser.driver import BrowserDriver


async def pins(domains: tuple[str, ...]) -> str:
    rules = []
    for domain in domains:
        if "*" in domain or not domain or "/" in domain:
            raise ResourceConflictError("Use exact hostnames in the browser domain list")
        try:
            values = await asyncio.get_running_loop().getaddrinfo(
                domain, 443, type=socket.SOCK_STREAM
            )
        except OSError:
            raise ResourceConflictError("Provider hostname could not be resolved") from None
        addresses = [ipaddress.ip_address(value[4][0]) for value in values]
        if not addresses or any(not address.is_global for address in addresses):
            raise ResourceConflictError("Browser access requires public provider addresses")
        address = str(addresses[0])
        rules.append(f"MAP {domain} {address if ':' not in address else '[' + address + ']'}")
    return ", ".join([*rules, "MAP * ~NOTFOUND", "EXCLUDE localhost"])


@dataclass
class Tab:
    page: Page
    driver: BrowserDriver
    context: BrowserContext


class BrowserFactory(Protocol):
    def open(
        self,
        definition: PlaybookDraft,
        state: dict[str, Any] | None = None,
        extra_domains: tuple[str, ...] = (),
        pin: bool = True,
    ) -> AbstractAsyncContextManager[Tab]: ...


class Launcher:
    @asynccontextmanager
    async def open(
        self,
        definition: PlaybookDraft,
        state: dict[str, Any] | None = None,
        extra_domains: tuple[str, ...] = (),
        pin: bool = True,
    ) -> AsyncIterator[Tab]:
        domains = tuple(dict.fromkeys((*definition.allowed_domains, *extra_domains)))
        policy = BrowserPolicy(
            allowed_domains=domains,
            allowed_actions=frozenset(BrowserActionKind),
            login_url_pattern=definition.login_url_pattern,
        )
        rules = await pins(domains) if pin else ""
        proxy = None
        proxy_url = os.environ.get("KINU_BROWSER_PROXY", "").strip()
        if proxy_url:
            parsed = urlsplit(proxy_url)
            proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
            if parsed.username:
                proxy["username"] = unquote(parsed.username)
            if parsed.password:
                proxy["password"] = unquote(parsed.password)
        arguments = [
            "--no-sandbox",
            "--no-proxy-server",
            "--disable-blink-features=AutomationControlled",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]
        if rules:
            arguments.insert(3, "--host-resolver-rules=" + rules)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=playwright.chromium.executable_path,
                args=arguments,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key in {"PATH", "HOME", "TMPDIR", "LANG"}
                },
            )
            try:
                context = await browser.new_context(
                    storage_state=cast(Any, state),
                    accept_downloads=False,
                    service_workers="block",
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        f"(KHTML, like Gecko) Chrome/{browser.version} Safari/537.36"
                    ),
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                await context.route_web_socket("**/*", lambda route: route.close())
                page = await context.new_page()
                context.on(
                    "page",
                    lambda opened: asyncio.create_task(opened.close()) if opened != page else None,
                )
                driver = BrowserDriver(page, policy)
                await driver.enforce_egress()
                yield Tab(page, driver, context)
            finally:
                await browser.close()
