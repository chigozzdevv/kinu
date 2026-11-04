from typing import Literal

from contracts import Contract, Identifier
from ingestion.mail import MailRule
from pydantic import AwareDatetime, Field, SecretStr, field_validator, model_validator

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALLBACK = "/connections/gmail/callback"


class Sender(Contract):
    sender: str = Field(min_length=3, max_length=254)
    provider_connection_id: Identifier

    @field_validator("sender")
    @classmethod
    def address(cls, value: str) -> str:
        return MailRule.exact_sender(value.strip())


class Mailbox(Contract):
    id: Identifier
    organisation_id: Identifier
    email: str
    senders: tuple[Sender, ...] = ()
    status: Literal["setup-required", "ready", "reauthentication-required", "disabled"] = (
        "setup-required"
    )
    connected_at: AwareDatetime
    last_checked_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)


class SenderUpdate(Contract):
    expected_revision: int = Field(ge=0)
    senders: tuple[Sender, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_senders(self) -> "SenderUpdate":
        if len({item.sender for item in self.senders}) != len(self.senders):
            raise ValueError("each sender can appear once")
        return self


class ForwardInbox(Contract):
    id: Identifier
    organisation_id: Identifier
    address: str = Field(min_length=5, max_length=254)
    created_at: AwareDatetime
    last_received_at: AwareDatetime | None = None
    receiving: bool = False
    confirmation_url: str | None = Field(
        default=None, pattern=r"^https://mail-settings\.google\.com/mail/vf-[A-Za-z0-9_?=&%.-]+$"
    )
    revision: int = Field(default=0, ge=0)


class GoogleConfig(Contract):
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    key: SecretStr = Field(min_length=32)

    @property
    def ready(self) -> bool:
        return self.client_id.endswith(".apps.googleusercontent.com") and bool(
            self.client_secret.get_secret_value()
        )


class Grant(Contract):
    access_token: SecretStr
    refresh_token: SecretStr
    expires_at: AwareDatetime


class Pending(Contract):
    verifier: SecretStr
    mailbox_id: Identifier | None
    revision: int | None
