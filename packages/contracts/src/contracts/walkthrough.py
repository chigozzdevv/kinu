from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class WalkthroughStatus(StrEnum):
    UPLOADING = "uploading"
    ANALYSING = "analysing"
    READY = "ready"
    FAILED = "failed"


class WalkthroughKind(StrEnum):
    VIDEO = "video"
    TEXT = "text"
    LINK = "link"


class TimedText(Contract):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_range(self) -> "TimedText":
        if self.end_seconds < self.start_seconds:
            raise ValueError("timed text must end after it starts")
        return self


class VideoShot(Contract):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "VideoShot":
        if self.end_seconds < self.start_seconds:
            raise ValueError("video shot must end after it starts")
        return self


class WalkthroughAnalysis(Contract):
    source_id: Identifier
    transcript: tuple[TimedText, ...] = ()
    screen_text: tuple[TimedText, ...] = ()
    shots: tuple[VideoShot, ...] = ()
    redaction_count: int = Field(default=0, ge=0)
    processor: str = Field(default="video-analysis", min_length=3, max_length=96)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def require_observations(self) -> "WalkthroughAnalysis":
        if not self.transcript and not self.screen_text:
            raise ValueError("walkthrough analysis requires transcript or screen text")
        return self


class WalkthroughSource(Contract):
    id: Identifier
    organisation_id: Identifier
    playbook_id: Identifier
    kind: WalkthroughKind = WalkthroughKind.VIDEO
    object_name: str | None = Field(default=None, min_length=1, max_length=768)
    resource: str = Field(min_length=5, max_length=1024)
    content_type: str = Field(pattern=r"^(video/[a-z0-9.+-]+|text/plain|text/uri-list)$")
    size: int = Field(ge=0, le=2_000_000_000)
    crc32c: str | None = Field(default=None, min_length=4, max_length=16)
    status: WalkthroughStatus
    operation: str | None = Field(default=None, max_length=1024)
    analysis: WalkthroughAnalysis | None = None
    failure: str | None = Field(default=None, max_length=1024)
    created_by: Identifier
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> "WalkthroughSource":
        uploaded_video = self.object_name is not None
        if uploaded_video and (
            self.kind is not WalkthroughKind.VIDEO
            or not self.content_type.startswith("video/")
            or self.size <= 0
            or self.crc32c is None
        ):
            raise ValueError("uploaded walkthroughs require immutable video metadata")
        if not uploaded_video and (
            self.status not in {WalkthroughStatus.READY, WalkthroughStatus.FAILED}
            or self.operation is not None
        ):
            raise ValueError("reference sources must be ready sanitised evidence")
        if (
            not uploaded_video
            and self.kind is WalkthroughKind.TEXT
            and not self.resource.startswith("sha256:")
        ):
            raise ValueError("text source resources contain only a content digest")
        if (
            not uploaded_video
            and self.kind in {WalkthroughKind.LINK, WalkthroughKind.VIDEO}
            and not self.resource.startswith("https://")
        ):
            raise ValueError("linked source resources require HTTPS")
        if self.status is WalkthroughStatus.UPLOADING and (self.operation or self.analysis):
            raise ValueError("uploading walkthrough cannot have analysis state")
        if self.status is WalkthroughStatus.ANALYSING and not self.operation:
            raise ValueError("analysing walkthrough requires a provider operation")
        if self.status is WalkthroughStatus.READY and self.analysis is None:
            raise ValueError("ready walkthrough requires analysis")
        if self.status is WalkthroughStatus.FAILED and not self.failure:
            raise ValueError("failed walkthrough requires a reason")
        if self.status is not WalkthroughStatus.FAILED and self.failure is not None:
            raise ValueError("only failed walkthroughs can retain a failure")
        return self
