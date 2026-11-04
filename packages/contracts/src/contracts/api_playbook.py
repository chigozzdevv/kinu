from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.http import HttpProviderApi
from contracts.playbook import Playbook, PlaybookState


class ApiPlaybookDraft(Contract):
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    http: HttpProviderApi

    @model_validator(mode="after")
    def validate_operations(self) -> "ApiPlaybookDraft":
        api = self.http
        if self.platform == "resend":
            raise ValueError("Use the built-in Resend API connection")
        if api.create_credential.method not in {"POST", "PUT", "PATCH"}:
            raise ValueError("Creation requires a write operation")
        if api.revoke_credential.method == "GET":
            raise ValueError("Revocation requires a write operation")
        if api.list_credentials.method != "GET":
            raise ValueError("Credential listing must be read-only")
        if not api.test_credential or not api.credential_auth:
            raise ValueError("Include a replacement-key identity check")
        if api.test_credential.method != "GET" or not api.test_credential.provider_id_field:
            raise ValueError("The identity check must be read-only and return the credential ID")
        return self


class ApiPlaybook(Playbook):
    pass


class ApiPlaybookVersion(Contract):
    id: Identifier
    organisation_id: Identifier
    playbook_id: Identifier
    number: int = Field(gt=0)
    definition: ApiPlaybookDraft
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: PlaybookState
    source_ids: tuple[Identifier, ...] = ()
    published_by: Identifier | None = None
    published_at: AwareDatetime | None = None
    created_by: Identifier
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_publication(self) -> "ApiPlaybookVersion":
        if (self.published_by is None) != (self.published_at is None):
            raise ValueError("Publisher and time must be set together")
        if self.state is PlaybookState.PUBLISHED and self.published_by is None:
            raise ValueError("Published playbooks require publisher identity and time")
        return self
