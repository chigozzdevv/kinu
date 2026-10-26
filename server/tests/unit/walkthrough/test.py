from datetime import UTC, datetime

import pytest
from connectors.base.errors import ConnectorError
from contracts import (
    TimedText,
    WalkthroughAnalysis,
    WalkthroughKind,
    WalkthroughSource,
    WalkthroughStatus,
)
from core.errors import PlaybookError
from core.playbook import WalkthroughService

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class Repository:
    def __init__(self) -> None:
        self.source: WalkthroughSource | None = None

    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]:
        if self.source is None:
            self.source = source
            return (source, True)
        return (self.source, False)

    async def get(
        self, organisation_id: str, playbook_id: str, source_id: str
    ) -> WalkthroughSource:
        assert self.source is not None
        assert (organisation_id, playbook_id, source_id) == (
            self.source.organisation_id,
            self.source.playbook_id,
            self.source.id,
        )
        return self.source

    async def replace(
        self, current: WalkthroughSource, changed: WalkthroughSource
    ) -> WalkthroughSource:
        assert self.source == current
        assert changed.revision == current.revision + 1
        self.source = changed
        return changed


class Uploads:
    def resource(self, object_name: str) -> str:
        return f"storage://walkthroughs/{object_name}"

    async def begin(self, object_name: str, content_type: str, size: int, crc32c: str) -> str:
        assert object_name.endswith("/walkthroughs/source_one/video")
        assert (content_type, size, crc32c) == ("video/mp4", 120, "ImIEBA==")
        return "https://storage.example.com/upload/session"

    async def verify(self, object_name: str, content_type: str, size: int, crc32c: str) -> str:
        assert object_name.endswith("/walkthroughs/source_one/video")
        assert (content_type, size, crc32c) == ("video/mp4", 120, "ImIEBA==")
        return "7"

    async def import_video(self, resource: str, object_name: str) -> tuple[str, str, int, str, str]:
        assert resource == "https://storage.example.com/customer-videos/rotation.mp4"
        assert object_name.endswith("/walkthroughs/source_one/video")
        return (
            "storage://walkthroughs/organisations/org_one/playbooks/playbook_one/walkthroughs/source_one/video",
            "9",
            120,
            "ImIEBA==",
            "video/mp4",
        )


class Video:
    async def start(self, resource: str) -> str:
        assert resource.startswith(("storage://walkthroughs/", "storage://customer-videos/"))
        return "operations/video-one"

    async def result(
        self, operation: str, source_id: str, now: datetime
    ) -> WalkthroughAnalysis | None:
        assert operation == "operations/video-one"
        return WalkthroughAnalysis(
            source_id=source_id,
            transcript=(TimedText(start_seconds=1, end_seconds=2, text="Open settings"),),
            created_at=now,
        )


class FailingVideo(Video):
    async def result(
        self, operation: str, source_id: str, now: datetime
    ) -> WalkthroughAnalysis | None:
        raise ConnectorError("video-analysis-failed", "Video Intelligence analysis failed")


@pytest.mark.anyio
async def test_walkthrough_upload_analysis_and_ready_handoff() -> None:
    repository = Repository()
    service = WalkthroughService(repository, Uploads(), Video(), "walkthroughs", lambda: NOW)
    source, upload_url, created = await service.begin(
        "org_one", "playbook_one", "source_one", "video/mp4", 120, "ImIEBA==", "user_one"
    )
    analysing = await service.complete("org_one", "playbook_one", "source_one")
    ready = await service.refresh("org_one", "playbook_one", "source_one")
    assert created is True
    assert upload_url.startswith("https://")
    assert source.status is WalkthroughStatus.UPLOADING
    assert analysing.status is WalkthroughStatus.ANALYSING
    assert analysing.resource.endswith("#7")
    assert ready.status is WalkthroughStatus.READY
    assert ready.analysis is not None
    assert ready.analysis.transcript[0].text == "Open settings"


@pytest.mark.anyio
async def test_cloud_storage_video_reference_is_analysed_and_generation_pinned() -> None:
    repository = Repository()
    service = WalkthroughService(repository, Uploads(), Video(), "walkthroughs", lambda: NOW)
    analysing, created = await service.reference_video(
        "org_one",
        "playbook_one",
        "source_one",
        "https://storage.example.com/customer-videos/rotation.mp4",
        "user_one",
    )
    ready = await service.refresh("org_one", "playbook_one", "source_one")
    assert created is True
    assert analysing.status is WalkthroughStatus.ANALYSING
    assert analysing.resource.endswith("/walkthroughs/source_one/video#9")
    assert ready.status is WalkthroughStatus.READY
    assert ready.analysis is not None


@pytest.mark.anyio
async def test_failed_video_analysis_is_persisted_without_a_server_error() -> None:
    repository = Repository()
    service = WalkthroughService(repository, Uploads(), FailingVideo(), "walkthroughs", lambda: NOW)
    await service.reference_video(
        "org_one",
        "playbook_one",
        "source_one",
        "https://storage.example.com/customer-videos/rotation.mp4",
        "user_one",
    )
    failed = await service.refresh("org_one", "playbook_one", "source_one")
    assert failed.status is WalkthroughStatus.FAILED
    assert failed.failure == "video analysis failed"
    assert failed.analysis is None


@pytest.mark.anyio
async def test_text_source_is_stored_as_sanitised_evidence_with_digest_only() -> None:
    repository = Repository()
    service = WalkthroughService(repository, Uploads(), Video(), "walkthroughs", lambda: NOW)
    source, created = await service.register(
        "org_one",
        "playbook_one",
        "source_text",
        WalkthroughKind.TEXT,
        "Open settings. password=do-not-store then click Create.",
        "user_one",
    )
    assert created is True
    assert source.resource.startswith("sha256:")
    assert "do-not-store" not in source.model_dump_json()
    assert source.analysis is not None
    assert source.analysis.transcript[0].text == "Open settings. [REDACTED] then click Create."
    assert source.analysis.redaction_count == 1


@pytest.mark.anyio
async def test_link_source_rejects_credentials_or_queries_in_its_url() -> None:
    service = WalkthroughService(Repository(), Uploads(), Video(), "walkthroughs", lambda: NOW)
    with pytest.raises(PlaybookError, match="cannot contain credentials or queries"):
        await service.register(
            "org_one",
            "playbook_one",
            "source_link",
            WalkthroughKind.LINK,
            "Open credential settings",
            "user_one",
            "https://docs.example.com/rotation?token=do-not-store",
        )
