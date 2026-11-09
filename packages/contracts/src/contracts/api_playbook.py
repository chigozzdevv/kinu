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
        if api.create_credential.method not in {"POST", "PUT", "PATCH"}:
            raise ValueError("Creation requires a write operation")
        if api.revoke_credential.method == "GET":
            raise ValueError("Revocation requires a write operation")
        if api.list_credentials.method != "GET":
            raise ValueError("Credential listing must be read-only")
        if not api.test_credential or not api.credential_auth:
            raise ValueError("Include a replacement-key identity check")
        if api.test_credential.method != "GET":
            raise ValueError("The credential check must be read-only")
        if api.credential_identity_mode == "response" and not api.test_credential.provider_id_field:
            raise ValueError("The identity check must return the credential ID")
        if api.credential_identity_mode == "creation-receipt" and (
            api.test_credential.provider_id_field is not None
            or not api.test_credential.response_matches
        ):
            raise ValueError("The credential check must declare an exact accepted response")
        return self


def validate_creation_scopes(definition: ApiPlaybookDraft) -> None:
    """Ensure the playbook's declared permissions match what Kinu records."""
    if definition.http.credential_scopes is not None:
        return
    if not _contains_scopes(definition.http.create_credential.body):
        raise ValueError("Bind ${scopes} in the create request or declare fixed permissions")


def _contains_scopes(value: object) -> bool:
    if value == "${scopes}":
        return True
    if isinstance(value, dict):
        return any(_contains_scopes(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_scopes(item) for item in value)
    return False


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
