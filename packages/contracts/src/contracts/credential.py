from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class SecretResourceMetadata(Contract):
    reference: str = Field(pattern=r"^(?:arn:[A-Za-z0-9_:/+.,=@-]+|[a-z][a-z0-9+.-]*://[^\s]+)$")
    display_name: str = Field(min_length=1, max_length=256)


class SecretVersionMetadata(Contract):
    reference: str = Field(pattern=r"^(?:arn:[A-Za-z0-9_:/+.,=@-]+|[a-z][a-z0-9+.-]*://[^\s]+)$")
    state: str = Field(min_length=1, max_length=32)
    created_at: AwareDatetime | None = None


class ManagedCredential(Contract):
    id: Identifier
    organisation_id: Identifier
    connection_id: Identifier
    secret_store_connection_id: Identifier
    secret_resource: str = Field(min_length=1, max_length=1024)
    secret_reference: str = Field(min_length=1, max_length=1024)
    provider: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    provider_id: str | None = Field(default=None, max_length=256)
    provider_display_name: str | None = Field(default=None, max_length=256)
    scopes: frozenset[str] = frozenset()
    consumer_ids: tuple[Identifier, ...] = ()
    active_generation_id: Identifier | None = None
    control_version: Identifier
    expires_at: AwareDatetime | None = None
    rotation_due_at: AwareDatetime | None = None
    last_observed_at: AwareDatetime | None = None
    metadata_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    archived_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_rotation_due(self) -> "ManagedCredential":
        if self.secret_reference.partition("/versions/")[0] != self.secret_resource:
            raise ValueError("credential secret reference must belong to its secret resource")
        if len(set(self.consumer_ids)) != len(self.consumer_ids):
            raise ValueError("credential consumer IDs must be unique")
        if self.rotation_due_at is not None and self.expires_at is None:
            raise ValueError("a credential rotation due time requires an expiry")
        if (
            self.rotation_due_at is not None
            and self.expires_at is not None
            and self.rotation_due_at > self.expires_at
        ):
            raise ValueError("credential rotation must be due no later than expiry")
        return self
