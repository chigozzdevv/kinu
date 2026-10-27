from contracts import Contract, Identifier
from pydantic import AwareDatetime, Field


class AwsLink(Contract):
    id: Identifier
    organisation_id: Identifier
    account_id: str = Field(pattern=r"^\d{12}$")
    role_arn: str = Field(pattern=r"^arn:aws:iam::\d{12}:role/[A-Za-z0-9_+=,.@/-]+$")
    external_id: str = Field(min_length=32, max_length=128)
    region: str = Field(pattern=r"^[a-z]{2}(?:-[a-z]+)+-\d$")
    confirmed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    revision: int = Field(default=0, ge=0)
