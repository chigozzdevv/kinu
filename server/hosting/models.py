from typing import Literal

from contracts import Contract
from pydantic import Field, SecretStr

Platform = Literal["vercel", "railway", "render"]


class Begin(Contract):
    platform: Platform
    display_name: str = Field(min_length=1, max_length=100)
    token: SecretStr | None = Field(default=None, max_length=16384)


class Selection(Contract):
    expected_revision: int | None = Field(default=None, ge=0)
    project_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
