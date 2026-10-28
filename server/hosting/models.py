from typing import Literal

from contracts import Contract, Identifier
from pydantic import Field, SecretStr

Platform = Literal["vercel", "railway", "render"]


class Begin(Contract):
    platform: Platform
    display_name: str = Field(min_length=1, max_length=100)
    token: SecretStr | None = Field(default=None, max_length=16384)
    connection_id: Identifier | None = None
    expected_revision: int | None = Field(default=None, ge=0)


class Selection(Contract):
    expected_revision: int | None = Field(default=None, ge=0)
    project_ids: tuple[str, ...] = Field(min_length=1, max_length=100)


class Mapping(Contract):
    variable_reference: str = Field(min_length=1, max_length=1024)
    provider_connection_id: Identifier
    provider_id: str = Field(min_length=1, max_length=256)
    health_url: str | None = Field(default=None, max_length=2048)


class Import(Contract):
    project_id: str = Field(min_length=1, max_length=256)
    mappings: tuple[Mapping, ...] = Field(min_length=1, max_length=30)
