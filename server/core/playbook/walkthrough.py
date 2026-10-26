import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from contracts import (
    TimedText,
    WalkthroughAnalysis,
    WalkthroughKind,
    WalkthroughSource,
    WalkthroughStatus,
)

from core.errors import PlaybookError


class WalkthroughRepository(Protocol):
    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]: ...

    async def get(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource: ...

    async def replace(
        self,
        current: WalkthroughSource,
        changed: WalkthroughSource,
    ) -> WalkthroughSource: ...


class UploadProvider(Protocol):
    def resource(self, object_name: str) -> str: ...

    async def begin(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str: ...

    async def verify(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str: ...

    async def import_video(
        self, resource: str, object_name: str
    ) -> tuple[str, str, int, str, str]: ...


class VideoProvider(Protocol):
    async def start(self, resource: str) -> str: ...

    async def result(
        self,
        operation: str,
        source_id: str,
        now: datetime,
    ) -> WalkthroughAnalysis | None: ...


class WalkthroughService:
    def __init__(
        self,
        repository: WalkthroughRepository,
        uploads: UploadProvider,
        video: VideoProvider,
        bucket: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._uploads = uploads
        self._video = video
        self._bucket = bucket
        self._clock = clock

    async def register(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
        kind: WalkthroughKind,
        content: str,
        actor_id: str,
        resource_url: str | None = None,
    ) -> tuple[WalkthroughSource, bool]:
        if kind is WalkthroughKind.TEXT:
            resource = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
            content_type = "text/plain"
        else:
            resource = _source_url(resource_url)
            content_type = "text/uri-list"
        sanitised, redactions = sanitise_source_text(content)
        now = self._clock()
        candidate = WalkthroughSource(
            id=source_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            kind=kind,
            resource=resource,
            content_type=content_type,
            size=len(content.encode()),
            status=WalkthroughStatus.READY,
            analysis=WalkthroughAnalysis(
                source_id=source_id,
                transcript=(TimedText(start_seconds=0, end_seconds=0, text=sanitised),),
                redaction_count=redactions,
                processor="kinu-source-sanitizer",
                created_at=now,
            ),
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        return await self._repository.reserve(candidate)

    async def begin(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
        content_type: str,
        size: int,
        crc32c: str,
        actor_id: str,
    ) -> tuple[WalkthroughSource, str, bool]:
        now = self._clock()
        object_name = (
            f"organisations/{organisation_id}/playbooks/{playbook_id}/"
            f"walkthroughs/{source_id}/video"
        )
        candidate = WalkthroughSource(
            id=source_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            kind=WalkthroughKind.VIDEO,
            object_name=object_name,
            resource=self._uploads.resource(object_name),
            content_type=content_type,
            size=size,
            crc32c=crc32c,
            status=WalkthroughStatus.UPLOADING,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        source, created = await self._repository.reserve(candidate)
        if source.status is not WalkthroughStatus.UPLOADING:
            raise PlaybookError("walkthrough upload is already complete")
        session = await self._uploads.begin(object_name, content_type, size, crc32c)
        return source, session, created

    async def complete(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource:
        source = await self._repository.get(organisation_id, playbook_id, source_id)
        if source.status is not WalkthroughStatus.UPLOADING:
            return source
        if source.object_name is None or source.crc32c is None:
            raise PlaybookError("uploaded walkthrough metadata is incomplete")
        generation = await self._uploads.verify(
            source.object_name,
            source.content_type,
            source.size,
            source.crc32c,
        )
        immutable = f"{source.resource}#{generation}"
        operation = await self._video.start(source.resource)
        changed = source.model_copy(
            update={
                "resource": immutable,
                "status": WalkthroughStatus.ANALYSING,
                "operation": operation,
                "updated_at": self._clock(),
                "revision": source.revision + 1,
            }
        )
        return await self._repository.replace(source, changed)

    async def reference_video(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
        resource: str,
        actor_id: str,
    ) -> tuple[WalkthroughSource, bool]:
        object_name = (
            f"organisations/{organisation_id}/playbooks/{playbook_id}/"
            f"walkthroughs/{source_id}/video"
        )
        canonical, generation, size, crc32c, content_type = await self._uploads.import_video(
            resource, object_name
        )
        now = self._clock()
        candidate = WalkthroughSource(
            id=source_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            kind=WalkthroughKind.VIDEO,
            object_name=object_name,
            resource=f"{canonical}#{generation}",
            content_type=content_type,
            size=size,
            crc32c=crc32c,
            status=WalkthroughStatus.ANALYSING,
            operation="pending",
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        source, created = await self._repository.reserve(candidate)
        if not created:
            return source, False
        try:
            operation = await self._video.start(canonical)
        except Exception:
            failed = source.model_copy(
                update={
                    "status": WalkthroughStatus.FAILED,
                    "operation": None,
                    "failure": "video analysis could not start",
                    "updated_at": self._clock(),
                    "revision": source.revision + 1,
                }
            )
            await self._repository.replace(source, failed)
            raise
        analysing = source.model_copy(
            update={
                "operation": operation,
                "updated_at": self._clock(),
                "revision": source.revision + 1,
            }
        )
        return await self._repository.replace(source, analysing), True

    async def refresh(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource:
        source = await self._repository.get(organisation_id, playbook_id, source_id)
        if source.status is not WalkthroughStatus.ANALYSING:
            return source
        if source.operation is None:
            raise PlaybookError("walkthrough analysis operation is missing")
        try:
            analysis = await self._video.result(source.operation, source.id, self._clock())
        except Exception:
            failed = source.model_copy(
                update={
                    "status": WalkthroughStatus.FAILED,
                    "failure": "video analysis failed",
                    "updated_at": self._clock(),
                    "revision": source.revision + 1,
                }
            )
            return await self._repository.replace(source, failed)
        if analysis is None:
            return source
        changed = source.model_copy(
            update={
                "status": WalkthroughStatus.READY,
                "analysis": analysis,
                "updated_at": self._clock(),
                "revision": source.revision + 1,
            }
        )
        return await self._repository.replace(source, changed)

    async def ready(
        self,
        organisation_id: str,
        playbook_id: str,
        source_ids: tuple[str, ...],
    ) -> tuple[WalkthroughSource, ...]:
        values = []
        for source_id in source_ids:
            source = await self.refresh(organisation_id, playbook_id, source_id)
            if source.status is not WalkthroughStatus.READY or source.analysis is None:
                raise PlaybookError(f"walkthrough {source_id} analysis is not ready")
            values.append(source)
        return tuple(values)


_SOURCE_REDACTIONS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(
        r"(?i)\b(?:authorization|password|passphrase|api[_-]?key|credential|secret|token)\b\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|sk_(?:live|test)_[A-Za-z0-9]{16,})\b"
    ),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)


def sanitise_source_text(value: str) -> tuple[str, int]:
    text = value.strip()
    if not text or len(text) > 100_000:
        raise PlaybookError("source instructions must contain between 1 and 100000 characters")
    redactions = 0
    for pattern in _SOURCE_REDACTIONS:
        text, count = pattern.subn("[REDACTED]", text)
        redactions += count
    return text, redactions


def _source_url(value: str | None) -> str:
    if value is None:
        raise PlaybookError("linked sources require a resource URL")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PlaybookError("source URLs must be HTTPS and cannot contain credentials or queries")
    return value
