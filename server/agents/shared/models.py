from typing import Any, Literal

from contracts import PageCheckpoint, PlaybookDraft, SecureField, Selector, Stage, StepOutput
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode


class PlaybookAgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    stage: Literal["create", "revoke"]
    tool: Literal[
        "browser.navigate",
        "browser.click",
        "browser.fill",
        "browser.secure-capture",
        "browser.revokeCredential",
    ] = Field(
        description=(
            "Use browser.secure-capture only with create-credential; use "
            "browser.revokeCredential only with revoke-credential."
        )
    )
    operation: str = Field(min_length=1, max_length=96)
    objective: str = Field(min_length=1, max_length=1024)
    parameters: dict[str, str | int | bool | tuple[str, ...]] = Field(default_factory=dict)
    protected: bool = False
    evidence_checks: list[str] = Field(min_length=1)
    effect: Literal["none", "create-credential", "revoke-credential"] = Field(
        description=(
            "create-credential requires a create-stage secure-capture step; revoke-credential "
            "requires a revoke-stage browser.revokeCredential step."
        )
    )
    selectors: tuple[Selector, ...] = Field(default=(), max_length=1)
    checkpoint: PageCheckpoint
    secure_field: SecureField | None = Field(
        default=None,
        description="Required only for the create-credential secure-capture step.",
    )
    outputs: tuple[StepOutput, ...] = ()
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    retry_limit: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="before")
    @classmethod
    def keep_approvals_in_controls(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalised = dict(value)
        normalised["protected"] = False
        return normalised

    @model_validator(mode="after")
    def validate_security_pairing(self) -> "PlaybookAgentStep":
        from contracts import PlaybookStep

        PlaybookStep.model_validate(self.model_dump(mode="json"))
        return self


class PlaybookAgentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=64)
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    steps: tuple[PlaybookAgentStep, ...] = Field(min_length=1)
    login_url_pattern: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_browser_procedure(self) -> "PlaybookAgentDraft":
        PlaybookDraft.model_validate(self.model_dump(mode="json"))
        return self

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )
        _remove_unique_items(schema)
        return schema


def _remove_unique_items(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("uniqueItems", None)
        for nested in value.values():
            _remove_unique_items(nested)
    elif isinstance(value, list):
        for nested in value:
            _remove_unique_items(nested)


class InventoryAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str
    declared_consumers: list[str] = Field(default_factory=list)
    observed_consumers: list[str] = Field(default_factory=list)
    missing_inventory: list[str] = Field(default_factory=list)
    stale_inventory: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    conclusion: str


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["plan", "recovery", "escalate"]
    strategy: Literal["parallel", "dual-slot", "immediate", "multi-consumer"] | None = None
    observation_seconds: int | None = Field(default=None, ge=60, le=604800)
    ordered_stages: list[str] | None = None
    recovery_actions: list[str] | None = None
    recovery_id: str | None = None
    recovery_mode: Literal["retry", "rollback", "rollforward", "cleanup", "escalate"] | None = None
    eligible: bool | None = None
    rationale: str

    @model_validator(mode="after")
    def validate_branch(self) -> "PlannerOutput":
        if self.decision == "plan":
            if (
                self.strategy is None
                or self.observation_seconds is None
                or self.ordered_stages != [stage.value for stage in Stage]
                or not self.recovery_actions
            ):
                raise ValueError("planning output is incomplete")
        elif self.recovery_id is None or self.recovery_mode is None or self.eligible is None:
            raise ValueError("recovery output is incomplete")
        return self


class OperatorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    ready: bool
    expected_checkpoint: str
    drift_detected: bool
    pause_reason: str | None = None
