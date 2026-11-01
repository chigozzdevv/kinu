import ipaddress
from contextlib import suppress
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from connectors.base.errors import ConnectorError
from connectors.http.transport import PublicTransport, public_address

from core.errors import PlaybookError


def source_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.hostname.lower() in {"localhost", "localhost.localdomain"}
            or url.username is not None
            or url.password is not None
            or url.query
            or url.port not in {None, 443}
            or len(value) > 1024
        ):
            raise ValueError
        try:
            ipaddress.ip_address(url.hostname)
        except ValueError:
            pass
        else:
            if not public_address(url.hostname):
                raise ValueError
    except ValueError:
        raise PlaybookError(
            "Use a public HTTPS link without credentials or query parameters"
        ) from None
    return urlunsplit(url._replace(fragment=""))


class PageText(HTMLParser):
    ignored = frozenset(
        {
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "video",
            "audio",
            "iframe",
            "nav",
            "footer",
        }
    )

    def __init__(self, origin: str) -> None:
        super().__init__(convert_charrefs=True)
        self.origin = origin
        self.parts: list[str] = []
        self.hidden: dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.ignored:
            self.hidden[tag] = self.hidden.get(tag, 0) + 1
        if self.hidden:
            return
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "pre", "tr"}:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                with suppress(PlaybookError):
                    self.parts.append(" " + source_url(urljoin(self.origin, href)) + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.hidden:
            self.hidden[tag] -= 1
            if not self.hidden[tag]:
                del self.hidden[tag]

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


class Links:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client

    async def read(self, url: str) -> str:
        try:
            if self.client:
                return await self.fetch(self.client, url)
            async with httpx.AsyncClient(transport=PublicTransport(), trust_env=False) as client:
                return await self.fetch(client, url)
        except (ConnectorError, httpx.HTTPError, UnicodeError):
            raise PlaybookError(
                "Could not read this link. Paste the relevant instructions instead."
            ) from None

    async def fetch(self, client: httpx.AsyncClient, url: str) -> str:
        current = source_url(url)
        for _ in range(4):
            async with client.stream(
                "GET",
                current,
                headers={
                    "Accept": "text/html,text/plain,text/markdown,application/json",
                    "User-Agent": "Kinu",
                },
                timeout=15,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    current = source_url(urljoin(current, response.headers.get("location", "")))
                    continue
                if response.status_code != 200:
                    raise PlaybookError("Could not read this link. Paste the instructions instead.")
                media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media not in {
                    "text/html",
                    "text/plain",
                    "text/markdown",
                    "application/xhtml+xml",
                    "application/json",
                }:
                    raise PlaybookError("Use a link to a text page, or paste the instructions")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 512_000:
                        raise PlaybookError(
                            "This page is too large. Paste the relevant steps instead."
                        )
                text = bytes(content).decode(response.encoding or "utf-8", errors="replace")
                if media in {"text/html", "application/xhtml+xml"}:
                    page = PageText(current)
                    page.feed(text)
                    text = "".join(page.parts)
                return "\n".join(
                    " ".join(line.split()) for line in text.splitlines() if line.strip()
                )
        raise PlaybookError(
            "This link redirects too many times. Use the documentation URL directly."
        )
