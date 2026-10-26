from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.plan import OperationStep
from contracts.state import Stage


class PlaybookState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    REVIEW = "review-required"


class PlaybookEffect(StrEnum):
    NONE = "none"
    CREATE_CREDENTIAL = "create-credential"
    REVOKE_CREDENTIAL = "revoke-credential"


class SelectorKind(StrEnum):
    ROLE = "role"
    LABEL = "label"
    FIELD = "field"
    TEXT = "text"
    TEST_ID = "test-id"
    CSS = "css"


class Selector(Contract):
    kind: SelectorKind
    value: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=256)
    exact: bool = True


class PageCheckpoint(Contract):
    url_pattern: str = Field(min_length=1, max_length=1024)
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()


class SecureField(Contract):
    name: Identifier
    selector: Selector
    provider_id_selector: Selector
    provider_id_attribute: str = Field(default="text", pattern=r"^(text|value|href)$")
    provider_display_name_selector: Selector | None = None


class StepOutput(Contract):
    name: Identifier
    selector: Selector
    attribute: str = Field(default="text", pattern=r"^(text|value)$")


class PlaybookStep(OperationStep):
    effect: PlaybookEffect = PlaybookEffect.NONE
    selectors: tuple[Selector, ...] = ()
    checkpoint: PageCheckpoint | None = None
    secure_field: SecureField | None = None
    outputs: tuple[StepOutput, ...] = ()
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    retry_limit: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="after")
    def validate_capture(self) -> "PlaybookStep":
        navigation = self.tool == "browser.navigate"
        selection = self.tool == "browser.select"
        if navigation != (self.operation == "navigate"):
            raise ValueError("browser navigation must use the navigate tool and operation")
        if selection != (self.operation == "select"):
            raise ValueError("browser selection must use the select tool and operation")
        if navigation:
            if self.selectors:
                raise ValueError("browser navigation must not declare a selector")
            if not isinstance(self.parameters.get("url"), str):
                raise ValueError("browser navigation requires a URL parameter")
        if selection:
            if len(self.selectors) != 1:
                raise ValueError("browser selection requires exactly one selector")
            if not isinstance(self.parameters.get("value"), str):
                raise ValueError("browser selection requires an option label")
        if self.effect == PlaybookEffect.CREATE_CREDENTIAL and (
            self.stage != Stage.CREATE
            or self.tool != "browser.secure-capture"
            or self.secure_field is None
        ):
            raise ValueError("credential creation must use secure capture in the create stage")
        if self.secure_field is not None and self.effect != PlaybookEffect.CREATE_CREDENTIAL:
            raise ValueError("secure fields require the create-credential effect")
        if (
            self.effect == PlaybookEffect.CREATE_CREDENTIAL
            and self.secure_field is not None
            and self.selectors
            and self.selectors[0]
            in {self.secure_field.selector, self.secure_field.provider_id_selector}
        ):
            raise ValueError(
                "secure capture must target the credential creation control, not captured output"
            )
        if self.effect == PlaybookEffect.REVOKE_CREDENTIAL and (
            self.stage != Stage.REVOKE
            or self.tool != "browser.revokeCredential"
            or self.secure_field is not None
        ):
            raise ValueError("credential revocation must use the protected browser revoke tool")
        if self.tool.startswith("browser.") and not navigation and not self.selectors:
            raise ValueError("browser actions require deterministic selectors")
        if self.tool.startswith("browser.") and len(self.selectors) > 1:
            raise ValueError("each browser step must target exactly one control")
        if self.tool.startswith("browser.") and self.checkpoint is None:
            raise ValueError("browser actions require a deterministic page checkpoint")
        return self


class PlaybookDraft(Contract):
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=64)
    allowed_domains: tuple[str, ...] = ()
    steps: tuple[PlaybookStep, ...] = Field(min_length=1)
    login_url_pattern: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="before")
    @classmethod
    def migrate_provider(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "platform" in value or "provider" not in value:
            return value
        migrated = dict(value)
        migrated["platform"] = migrated.pop("provider")
        migrated.pop("execution", None)
        migrated.pop("allowed_tools", None)
        migrated.pop("required_connections", None)
        migrated.pop("recovery", None)
        return migrated

    @model_validator(mode="after")
    def validate_browser_procedure(self) -> "PlaybookDraft":
        if not self.allowed_domains or not self.login_url_pattern:
            raise ValueError("browser playbooks require domains and a login URL pattern")
        if any(not step.tool.startswith("browser.") for step in self.steps):
            raise ValueError("playbooks contain browser steps only")
        if any(step.protected for step in self.steps):
            raise ValueError(
                "playbooks cannot declare approvals; credential controls protect operations"
            )
        creation = tuple(
            index
            for index, step in enumerate(self.steps)
            if step.effect == PlaybookEffect.CREATE_CREDENTIAL
        )
        if len(creation) != 1:
            raise ValueError("browser playbooks require exactly one secure credential creation")
        if any(step.stage == Stage.CREATE for step in self.steps[creation[0] + 1 :]):
            raise ValueError("secure credential creation must be the final create-stage step")
        revocation = tuple(
            step for step in self.steps if step.effect == PlaybookEffect.REVOKE_CREDENTIAL
        )
        if len(revocation) != 1:
            raise ValueError("browser playbooks require exactly one credential revocation")
        secure = self.steps[creation[0]].secure_field
        if secure is None:
            raise ValueError("browser playbooks require secure credential capture")
        if self.steps[creation[0]].outputs:
            raise ValueError("secure credential creation cannot extract step outputs")
        if any(
            output.selector == secure.selector for step in self.steps for output in step.outputs
        ):
            raise ValueError("playbook outputs cannot target the secure credential field")
        if tuple(dict.fromkeys(step.id for step in self.steps)) != tuple(
            step.id for step in self.steps
        ):
            raise ValueError("playbook step IDs must be unique")
        return self


class PlaybookVersion(Contract):
    id: Identifier
    organisation_id: Identifier
    playbook_id: Identifier
    number: int = Field(gt=0)
    definition: PlaybookDraft
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: PlaybookState
    source_ids: tuple[Identifier, ...] = ()
    published_by: Identifier | None = None
    published_at: AwareDatetime | None = None
    created_by: Identifier
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_publication(self) -> "PlaybookVersion":
        published = self.published_by is not None and self.published_at is not None
        if (self.published_by is None) != (self.published_at is None):
            raise ValueError("playbook publisher identity and time must be set together")
        if self.state is PlaybookState.PUBLISHED and not published:
            raise ValueError("published playbooks require publisher identity and time")
        return self


class Playbook(Contract):
    id: Identifier
    organisation_id: Identifier
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=64)
    latest_version: int = Field(default=0, ge=0)
    latest_version_id: Identifier | None = None
    active_version_id: Identifier | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    archived_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)
