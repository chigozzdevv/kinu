from pydantic import AwareDatetime, Field

from contracts.base import Contract, Identifier
from contracts.state import GenerationState


class CredentialGeneration(Contract):
    id: Identifier
    organisation_id: Identifier
    credential_id: Identifier
    provider_id: str | None = Field(default=None, max_length=256)
    provider_display_name: str | None = Field(default=None, max_length=256)
    fingerprint: str | None = Field(default=None, min_length=8, max_length=256)
    scopes: frozenset[str] = frozenset()
    state: GenerationState
    attempt_id: Identifier
    secret_reference: str | None = Field(default=None, max_length=1024)
    predecessor_id: Identifier | None = None
    successor_id: Identifier | None = None
    expires_at: AwareDatetime | None = None
    last_observed_at: AwareDatetime | None = None
    metadata_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    created_at: AwareDatetime
    revoked_at: AwareDatetime | None = None
