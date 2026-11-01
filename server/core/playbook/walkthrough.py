import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    TimedText,
    WalkthroughAnalysis,
    WalkthroughKind,
    WalkthroughSource,
    WalkthroughStatus,
)

from core.errors import PlaybookError, ResourceNotFoundError
from core.playbook.links import Links, source_url


class WalkthroughRepository(Protocol):
    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]: ...

    async def get(
        self, organisation_id: str, playbook_id: str, source_id: str
    ) -> WalkthroughSource: ...


class WalkthroughService:
    def __init__(
        self,
        repository: WalkthroughRepository,
        clock: Callable[[], datetime],
        links: Links | None = None,
    ) -> None:
        self._repository, self._clock, self._links = repository, clock, links or Links()

    async def register(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
        kind: WalkthroughKind,
        content: str | None,
        actor_id: str,
        resource_url: str | None = None,
    ) -> tuple[WalkthroughSource, bool]:
        if kind is WalkthroughKind.TEXT and content and not resource_url:
            resource = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
            content_type = "text/plain"
        elif kind is WalkthroughKind.LINK and not content and resource_url:
            resource, content_type = source_url(resource_url), "text/uri-list"
        else:
            raise PlaybookError("Provide instructions or a public documentation link")
        try:
            existing = await self._repository.get(organisation_id, playbook_id, source_id)
        except ResourceNotFoundError:
            existing = None
        if existing:
            if existing.kind != kind or existing.resource != resource:
                raise PlaybookError("This source already identifies different instructions")
            return existing, False
        text = content if kind is WalkthroughKind.TEXT else await self._links.read(resource)
        sanitised, redactions = sanitise_source_text(text or "")
        now = self._clock()
        return await self._repository.reserve(
            WalkthroughSource(
                id=source_id,
                organisation_id=organisation_id,
                playbook_id=playbook_id,
                kind=kind,
                resource=resource,
                content_type=content_type,
                size=len((text or "").encode()),
                status=WalkthroughStatus.READY,
                analysis=WalkthroughAnalysis(
                    source_id=source_id,
                    transcript=tuple(
                        TimedText(
                            start_seconds=0, end_seconds=0, text=sanitised[index : index + 4000]
                        )
                        for index in range(0, len(sanitised), 4000)
                    ),
                    redaction_count=redactions,
                    processor="kinu-source-sanitizer",
                    created_at=now,
                ),
                created_by=actor_id,
                created_at=now,
                updated_at=now,
            )
        )

    async def refresh(self, org: str, playbook: str, source: str) -> WalkthroughSource:
        return await self._repository.get(org, playbook, source)

    async def ready(
        self, organisation_id: str, playbook_id: str, source_ids: tuple[str, ...]
    ) -> tuple[WalkthroughSource, ...]:
        if not source_ids or len(source_ids) > 6 or len(set(source_ids)) != len(source_ids):
            raise PlaybookError("Choose up to six distinct instruction sources")
        values = []
        for source_id in source_ids:
            source = await self.refresh(organisation_id, playbook_id, source_id)
            if source.kind not in {WalkthroughKind.TEXT, WalkthroughKind.LINK} or (
                source.status is not WalkthroughStatus.READY or source.analysis is None
            ):
                raise PlaybookError("Choose a ready text or link source")
            values.append(source)
        return tuple(values)


_SOURCE_REDACTIONS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(
        r"(?i)[\"']?\b(?:authorization|password|passphrase|api[_-]?key|credential|secret|token|cookie)"
        r"\b[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|sk_(?:live|test)_[A-Za-z0-9]{16,})\b"
    ),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)


def sanitise_source_text(value: str) -> tuple[str, int]:
    text = value.strip()
    if not text or len(text.encode()) > 100_000:
        raise PlaybookError("Use instructions containing between 1 and 100000 bytes")
    redactions = 0
    for pattern in _SOURCE_REDACTIONS:
        text, count = pattern.subn("[REDACTED]", text)
        redactions += count
    return text, redactions
