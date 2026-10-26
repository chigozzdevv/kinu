from enum import StrEnum

from pydantic import AwareDatetime, Field, field_validator

from contracts.base import Contract, Identifier


class MemberRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"


class MemberStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class Organisation(Contract):
    id: Identifier
    name: str = Field(min_length=1, max_length=120)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)


class OrganisationMembership(Contract):
    organisation: Organisation
    role: MemberRole


class AccountSession(Contract):
    organisations: tuple[OrganisationMembership, ...]


class AccountProfile(Contract):
    id: Identifier
    organisation_id: Identifier
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    connected_via: str = Field(min_length=1, max_length=80)
    role: MemberRole
    revision: int = Field(ge=0)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _email(value)


class TeamMember(Contract):
    id: Identifier
    organisation_id: Identifier
    display_name: str | None = Field(default=None, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    connected_via: str | None = Field(default=None, max_length=80)
    role: MemberRole
    status: MemberStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(ge=0)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _email(value)


class TeamInvitation(Contract):
    id: Identifier
    organisation_id: Identifier
    email: str = Field(min_length=3, max_length=320)
    role: MemberRole
    invited_by: Identifier
    expires_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime
    accepted_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _email(value)


def _email(value: str) -> str:
    normalised = value.strip().lower()
    local, separator, domain = normalised.rpartition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise ValueError("email address is invalid")
    return normalised
