import json
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.playbook import PlaybookEffect, Selector
from contracts.state import Stage


class BrowserStatus(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    TAKEOVER = "human-takeover"
    CAPTURING = "secure-capture"
    COMPLETE = "complete"
    FAILED = "failed"
    TERMINATED = "terminated"


class BrowserAccessMode(StrEnum):
    VIEW = "view"
    TAKEOVER = "takeover"


class BrowserActionKind(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    KEY = "key"
    WAIT = "wait"


class BrowserActionStatus(StrEnum):
    AUTHORIZED = "authorized"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ComputerUseActivityPhase(StrEnum):
    INPUT = "input"
    THOUGHT = "thought"
    RESPONSE = "response"
    PROPOSAL = "proposal"
    VALIDATION = "validation"
    EXECUTION = "execution"


class ComputerUseActivityStatus(StrEnum):
    SENT = "sent"
    STREAMING = "streaming"
    PROPOSED = "proposed"
    VALIDATED = "validated"
    SUCCEEDED = "succeeded"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"


class BrowserAction(Contract):
    id: Identifier
    session_id: Identifier
    kind: BrowserActionKind
    selector: Selector | None = None
    value: str | None = Field(default=None, max_length=4096)
    url: str | None = Field(default=None, max_length=2048)
    protected: bool = False
    expected_url: str | None = Field(default=None, max_length=2048)
    expected_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()
    fencing_token: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_target(self) -> "BrowserAction":
        if self.kind is BrowserActionKind.NAVIGATE and not self.url:
            raise ValueError("navigation requires a URL")
        if (
            self.kind in {BrowserActionKind.CLICK, BrowserActionKind.TYPE, BrowserActionKind.SELECT}
            and not self.selector
        ):
            raise ValueError("interactive actions require a selector")
        if self.kind in {BrowserActionKind.TYPE, BrowserActionKind.SELECT} and self.value is None:
            raise ValueError("input actions require a value")
        return self


class BrowserActionRecord(Contract):
    id: Identifier
    organisation_id: Identifier
    session_id: Identifier
    action: BrowserAction
    status: BrowserActionStatus
    error: str | None = Field(default=None, max_length=1024)
    authorised_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "BrowserActionRecord":
        terminal = self.status is not BrowserActionStatus.AUTHORIZED
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal browser actions require a completion time")
        if self.status is BrowserActionStatus.FAILED and not self.error:
            raise ValueError("failed browser actions require an error")
        return self


class BrowserActionResult(Contract):
    provider_id: str = Field(min_length=1, max_length=256)
    secret_reference: str | None = Field(default=None, max_length=1024)


class ComputerUseActivity(Contract):
    id: Identifier
    organisation_id: Identifier
    session_id: Identifier
    run_id: Identifier
    step_id: Identifier
    stage: Stage
    turn: int = Field(ge=1)
    phase: ComputerUseActivityPhase
    status: ComputerUseActivityStatus
    effect: PlaybookEffect | None = None
    prompt: str | None = Field(default=None, max_length=2048)
    instruction: str | None = Field(default=None, max_length=2048)
    image_reference: str | None = Field(default=None, max_length=1024)
    image_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    content: str | None = Field(default=None, max_length=8192)
    action: BrowserActionKind | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    intent: str | None = Field(default=None, max_length=2048)
    safety_decision: str | None = Field(default=None, max_length=64)
    result: BrowserActionResult | None = None
    target: str | None = Field(default=None, max_length=256)
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def validate_safe_shape(self) -> "ComputerUseActivity":
        if self.result is not None and (
            self.phase is not ComputerUseActivityPhase.EXECUTION
            or self.status is not ComputerUseActivityStatus.SUCCEEDED
        ):
            raise ValueError("Browser results require a successful execution")
        if bool(self.image_reference) != bool(self.image_digest):
            raise ValueError("Computer Use screenshots require both a reference and digest")
        if len(json.dumps(self.arguments, sort_keys=True, separators=(",", ":"))) > 8192:
            raise ValueError("Computer Use arguments exceed the activity limit")
        if self.phase is ComputerUseActivityPhase.INPUT:
            if (
                self.status is not ComputerUseActivityStatus.SENT
                or self.effect is None
                or not self.prompt
                or not self.instruction
                or not self.image_reference
                or not self.image_digest
            ):
                raise ValueError("Computer Use input requires the exact safe model request")
            if self.content is not None or self.action is not None or self.arguments:
                raise ValueError("Computer Use input cannot include model output")
        elif self.phase in {
            ComputerUseActivityPhase.THOUGHT,
            ComputerUseActivityPhase.RESPONSE,
        }:
            if self.effect is not None:
                raise ValueError("Computer Use model output cannot repeat the playbook effect")
            allowed = {
                ComputerUseActivityStatus.STREAMING,
                ComputerUseActivityStatus.COMPLETED,
            }
            if self.phase is ComputerUseActivityPhase.RESPONSE:
                allowed.add(ComputerUseActivityStatus.FAILED)
            if self.status not in allowed:
                raise ValueError("Computer Use model output status is invalid")
            if self.status is ComputerUseActivityStatus.STREAMING and not self.content:
                raise ValueError("streamed Computer Use output requires content")
            if self.prompt is not None or self.action is not None or self.arguments:
                raise ValueError("Computer Use model output has an invalid shape")
        elif self.phase is ComputerUseActivityPhase.PROPOSAL:
            if self.prompt is not None or self.effect is not None:
                raise ValueError("Computer Use proposals cannot repeat model input")
            if self.status is not ComputerUseActivityStatus.PROPOSED or self.action is None:
                raise ValueError("Computer Use proposals require a typed action")
        elif self.phase is ComputerUseActivityPhase.VALIDATION:
            if (
                self.effect is not None
                or self.action is None
                or self.status
                not in {
                    ComputerUseActivityStatus.VALIDATED,
                    ComputerUseActivityStatus.FAILED,
                }
            ):
                raise ValueError("Computer Use validation requires a typed action outcome")
        else:
            if self.prompt is not None or self.effect is not None or self.action is None:
                raise ValueError("Computer Use execution requires only a typed action")
            if self.status not in {
                ComputerUseActivityStatus.SUCCEEDED,
                ComputerUseActivityStatus.PAUSED,
                ComputerUseActivityStatus.FAILED,
            }:
                raise ValueError("Computer Use execution status is invalid")
        return self


class BrowserPolicy(Contract):
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    allowed_actions: frozenset[BrowserActionKind] = Field(min_length=1)
    protected_tools: frozenset[str] = frozenset()
    max_steps: int = Field(default=40, ge=1, le=200)
    allow_downloads: bool = False
    allow_uploads: bool = False
    allow_clipboard: bool = False
    login_url_pattern: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="before")
    @classmethod
    def migrate_protected_operations(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "protected_operations" not in value:
            return value
        migrated = dict(value)
        migrated.setdefault("protected_tools", migrated.pop("protected_operations"))
        return migrated


class BrowserSession(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    playbook_id: Identifier
    playbook_version: Identifier
    provider_connection_id: Identifier
    secret_store_connection_id: Identifier
    secret_resource: str = Field(min_length=1, max_length=1024)
    worker_instance: str | None = Field(default=None, max_length=512)
    internal_address: str | None = Field(default=None, max_length=128)
    status: BrowserStatus
    policy: BrowserPolicy
    fencing_token: int = Field(gt=0)
    step_count: int = Field(default=0, ge=0)
    model_paused: bool = True
    recording_paused: bool = True
    takeover_subject: str | None = Field(default=None, max_length=512)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    updated_at: AwareDatetime
    terminated_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "BrowserSession":
        if self.step_count > self.policy.max_steps:
            raise ValueError("browser session exceeded its step budget")
        if self.status is BrowserStatus.CAPTURING and (
            not self.model_paused or not self.recording_paused
        ):
            raise ValueError("secure capture requires model and recording barriers")
        if self.status is BrowserStatus.TERMINATED and self.terminated_at is None:
            raise ValueError("terminated browser sessions require a termination time")
        return self


class SecureCaptureResult(Contract):
    id: Identifier
    organisation_id: Identifier
    session_id: Identifier
    field_name: Identifier
    provider_id: str = Field(min_length=1, max_length=256)
    provider_display_name: str | None = Field(default=None, max_length=256)
    secret_reference: str = Field(min_length=1, max_length=1024)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    masked_value_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    captured_at: AwareDatetime


class BrowserAccessGrant(Contract):
    organisation_id: Identifier
    session_id: Identifier
    mode: BrowserAccessMode
    gateway_url: str = Field(min_length=12, max_length=2048)
    capability: str = Field(min_length=32)
    expires_at: AwareDatetime
    session: BrowserSession


class BrowserSecretKeyRequest(Contract):
    session_id: Identifier
    secret_store_connection_id: Identifier
    secret_resource: str = Field(min_length=1, max_length=1024)


class BrowserSecretKey(Contract):
    public_key: str = Field(min_length=512, max_length=4096)


class BrowserSecretAccessEnvelope(Contract):
    session_id: Identifier
    secret_store_connection_id: Identifier
    secret_resource: str = Field(min_length=1, max_length=1024)
    expires_at: AwareDatetime
    encrypted_key: str = Field(min_length=128, max_length=2048)
    nonce: str = Field(min_length=16, max_length=64)
    ciphertext: str = Field(min_length=32, max_length=8192)


class BrowserSecretAccessReceipt(Contract):
    expires_at: AwareDatetime


class SetupStatus(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    CAPTURING = "capturing"
    COMPLETE = "complete"
    TERMINATED = "terminated"


class SetupSession(Contract):
    id: Identifier
    organisation_id: Identifier
    connection_id: Identifier
    secret_container: str = Field(
        pattern=r"^(?:arn:[A-Za-z0-9_:/+.,=@-]+|[a-z][a-z0-9+.-]*://[^\s]+)$", max_length=1024
    )
    token_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    subject: str = Field(min_length=1, max_length=512)
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    worker_instance: str | None = Field(default=None, max_length=512)
    internal_address: str | None = Field(default=None, max_length=128)
    status: SetupStatus
    auth_reference: str | None = Field(default=None, max_length=1024)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    updated_at: AwareDatetime
    terminated_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "SetupSession":
        completed = self.status is SetupStatus.COMPLETE
        if completed != (self.auth_reference is not None):
            raise ValueError("a completed setup session requires its authentication reference")
        if self.status is SetupStatus.TERMINATED and self.terminated_at is None:
            raise ValueError("terminated setup sessions require a termination time")
        if self.status in {SetupStatus.READY, SetupStatus.CAPTURING} and (
            self.worker_instance is None or self.internal_address is None
        ):
            raise ValueError("an active setup session requires its worker binding")
        return self


class ConnectionWaiter(Contract):
    organisation_id: Identifier
    connection_id: Identifier
    run_ids: tuple[Identifier, ...] = ()
    revision: int = Field(default=0, ge=0)
