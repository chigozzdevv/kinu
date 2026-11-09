from typing import Any

from connectors.resend.config import definition
from contracts import Contract, HttpProviderApi, Identifier
from pydantic import Field, SecretStr, model_validator


class ProviderInput(Contract):
    display_name: str = Field(min_length=1, max_length=100)
    platform: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    http: HttpProviderApi | None = None
    api_playbook_id: Identifier | None = None
    api_playbook_version_id: Identifier | None = None
    token: SecretStr = Field(min_length=1, max_length=16384)
    domain_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def configure(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        book, version = value.get("api_playbook_id"), value.get("api_playbook_version_id")
        if (book is None) != (version is None):
            raise ValueError("Choose an API playbook and its published version")
        if book is not None:
            if value.get("http") is not None or value.get("domain_id"):
                raise ValueError("Use the selected API playbook configuration")
            return value
        if value.get("platform") == "resend":
            if not isinstance(value.get("domain_id"), str) or not value["domain_id"]:
                raise ValueError("Choose a verified Resend domain")
            configured = definition(value["domain_id"])
            supplied = value.get("http")
            if supplied is not None and HttpProviderApi.model_validate(supplied) != configured:
                raise ValueError("Use Kinu's Resend connection configuration")
            return {**value, "http": configured}
        if value.get("http") is None or value.get("domain_id"):
            raise ValueError("Custom providers require an HTTP definition")
        return value


class ResendAccess(Contract):
    token: SecretStr = Field(min_length=1, max_length=16384)


class ResendDomain(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    name: str = Field(min_length=1, max_length=253)
