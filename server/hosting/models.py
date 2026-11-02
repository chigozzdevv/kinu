from typing import Annotated, Literal

from contracts import Contract, ControlPreferences, Identifier
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
    initial_controls: ControlPreferences | None = None


class Provision(Contract):
    project_id: str = Field(min_length=1, max_length=256)
    variable_reference: str = Field(min_length=1, max_length=1024)
    provider_connection_id: Identifier
    health_url: str = Field(min_length=1, max_length=2048)
    initial_controls: ControlPreferences | None = None
    scopes: frozenset[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default=frozenset(), max_length=50
    )
