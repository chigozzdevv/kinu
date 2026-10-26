import re
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from contracts.base import Contract


class HttpAuthScheme(StrEnum):
    BEARER = "bearer"
    HEADER = "header"
    BASIC = "basic"


class HttpAuth(Contract):
    scheme: HttpAuthScheme
    header: str = Field(default="Authorization", min_length=1, max_length=128)
    prefix: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_header(self) -> "HttpAuth":
        if self.scheme is HttpAuthScheme.HEADER and self.header.lower() == "authorization":
            raise ValueError("header authentication requires an explicit API header name")
        return self


class HttpOperation(Contract):
    method: str = Field(pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    path: str = Field(min_length=1, max_length=1024, pattern=r"^/")
    success_statuses: tuple[int, ...] = Field(min_length=1)
    query: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    list_items: str | None = Field(default=None, max_length=256)
    provider_id_field: str | None = Field(default=None, max_length=128)
    secret_field: str | None = Field(default=None, max_length=128)
    name_field: str | None = Field(default=None, max_length=128)
    metadata_fields: dict[str, str] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def validate_statuses(self) -> "HttpOperation":
        if any(not 100 <= status <= 599 for status in self.success_statuses):
            raise ValueError("HTTP success statuses must be valid status codes")
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError(
                "provider operation paths must be origin-relative paths without queries"
            )
        if any(character.isspace() or ord(character) < 32 for character in self.path):
            raise ValueError("provider operation paths must not contain control characters")
        placeholders = re.findall(r"{([A-Za-z][A-Za-z0-9_]*)}", self.path)
        if "{" in re.sub(r"{[A-Za-z][A-Za-z0-9_]*}", "", self.path) or "}" in re.sub(
            r"{[A-Za-z][A-Za-z0-9_]*}", "", self.path
        ):
            raise ValueError("provider operation paths contain an invalid placeholder")
        if len(placeholders) != len(set(placeholders)):
            raise ValueError("provider operation paths must not repeat placeholders")
        unsupported = set(self.metadata_fields).difference(_METADATA_FIELDS)
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"provider metadata contains unsupported fields: {names}")
        if any(not value or len(value) > 128 for value in self.metadata_fields.values()):
            raise ValueError("provider metadata paths must be between 1 and 128 characters")
        return self


class HttpProviderApi(Contract):
    base_url: str = Field(min_length=12, max_length=1024)
    auth: HttpAuth
    list_credentials: HttpOperation
    create_credential: HttpOperation
    revoke_credential: HttpOperation
    test_credential: HttpOperation | None = None
    credential_auth: HttpAuth | None = None

    @model_validator(mode="after")
    def validate_api(self) -> "HttpProviderApi":
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "provider API must be an HTTPS origin without credentials or query data"
            )
        if any(character.isspace() or ord(character) < 32 for character in self.base_url):
            raise ValueError("provider API URL must not contain control characters")
        try:
            _ = parsed.port
        except ValueError as error:
            raise ValueError("provider API URL has an invalid port") from error
        if self.create_credential.provider_id_field is None:
            raise ValueError("create must declare the provider ID field")
        if self.create_credential.secret_field is None:
            raise ValueError("create must declare the secret field")
        if self.list_credentials.provider_id_field is None:
            raise ValueError("list must declare the provider ID field")
        if "{provider_id}" not in self.revoke_credential.path:
            raise ValueError("revoke path must include {provider_id}")
        if (self.test_credential is None) != (self.credential_auth is None):
            raise ValueError(
                "credential authentication requires both a test operation and auth scheme"
            )
        return self


_METADATA_FIELDS = frozenset(
    {
        "created_at",
        "disabled",
        "expires_at",
        "last_used_at",
        "kind",
        "scopes",
        "status",
    }
)
