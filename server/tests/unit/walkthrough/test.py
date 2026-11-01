from datetime import UTC, datetime

import httpx
import pytest
from contracts import WalkthroughKind, WalkthroughSource, WalkthroughStatus
from core.errors import PlaybookError, ResourceNotFoundError
from core.playbook import WalkthroughService
from core.playbook.links import Links, source_url

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Repository:
    def __init__(self) -> None:
        self.source: WalkthroughSource | None = None

    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]:
        self.source = source
        return source, True

    async def get(self, org: str, playbook: str, source: str) -> WalkthroughSource:
        if self.source and (org, playbook, source) == (
            self.source.organisation_id,
            self.source.playbook_id,
            self.source.id,
        ):
            return self.source
        raise ResourceNotFoundError("Source is unavailable")


async def test_text_is_sanitised_persisted_and_reused_without_losing_long_instructions() -> None:
    repository = Repository()
    service = WalkthroughService(repository, lambda: NOW)
    text = (
        'Open settings. password="do not store" and "api_key": "short-private". '
        + "Check permissions. " * 600
    )
    source, created = await service.register(
        "org_one", "playbook_one", "source_one", WalkthroughKind.TEXT, text, "actor_one"
    )
    assert created and source.status is WalkthroughStatus.READY and source.analysis
    assert (
        "do not store" not in source.model_dump_json()
        and "short-private" not in source.model_dump_json()
    )
    assert source.resource.startswith("sha256:") and len(source.analysis.transcript) > 1
    repeated, created = await service.register(
        "org_one", "playbook_one", "source_one", WalkthroughKind.TEXT, text, "actor_one"
    )
    assert not created and repeated == source
    assert await service.ready("org_one", "playbook_one", (source.id,)) == (source,)
    with pytest.raises(PlaybookError, match="different instructions"):
        await service.register(
            "org_one",
            "playbook_one",
            "source_one",
            WalkthroughKind.TEXT,
            "Different text",
            "actor_one",
        )


async def test_link_content_is_fetched_sanitised_and_frozen_as_source_evidence() -> None:
    calls: list[str] = []

    def response(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers.get("authorization") is None
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""
            <nav>Navigation noise</nav><script>private-script-value</script>
            <h1>API keys</h1><p>Open <a href="https://console.example.com/keys">Keys</a>.</p>
            <p>token=private-fixture then click Create.</p><video>Video instructions</video>
        """,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        service = WalkthroughService(Repository(), lambda: NOW, Links(http))
        source, _ = await service.register(
            "org_one",
            "playbook_one",
            "source_link",
            WalkthroughKind.LINK,
            None,
            "actor_one",
            "https://docs.example.com/keys#create",
        )
        assert source.analysis and source.resource == "https://docs.example.com/keys"
        content = source.analysis.transcript[0].text
        assert "https://console.example.com/keys" in content and "click Create" in content
        assert all(
            value not in content
            for value in (
                "Navigation noise",
                "private-script-value",
                "private-fixture",
                "Video instructions",
            )
        )
        await service.register(
            "org_one",
            "playbook_one",
            "source_link",
            WalkthroughKind.LINK,
            None,
            "actor_one",
            source.resource,
        )
        assert calls == [source.resource]


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/docs",
        "https://[::1]/docs",
        "https://localhost/docs",
        "https://192.168.1.2/docs",
        "http://docs.example.com",
        "https://user:password@docs.example.com",
        "https://docs.example.com/?token=private",
        "https://docs.example.com:8443/",
    ],
)
async def test_private_or_authenticated_documentation_urls_are_rejected(url: str) -> None:
    with pytest.raises(PlaybookError, match="public HTTPS"):
        source_url(url)


async def test_redirects_are_checked_before_the_next_request() -> None:
    calls = 0

    def response(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        with pytest.raises(PlaybookError):
            await Links(http).read("https://docs.example.com/keys")
    assert calls == 1


@pytest.mark.parametrize("media,text", [("video/mp4", "movie"), ("text/html", "x" * 512001)])
async def test_video_and_oversized_pages_are_rejected(media: str, text: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": media}, text=text)
        )
    ) as http:
        with pytest.raises(PlaybookError):
            await Links(http).read("https://docs.example.com/keys")


async def test_video_sources_cannot_be_registered() -> None:
    service = WalkthroughService(Repository(), lambda: NOW)
    with pytest.raises(PlaybookError, match="instructions or"):
        await service.register(
            "org_one",
            "playbook_one",
            "source_video",
            WalkthroughKind.VIDEO,
            None,
            "actor_one",
            "https://docs.example.com/movie.mp4",
        )
