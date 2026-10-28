from typing import Literal

from contracts import Contract
from pydantic import Field


class Project(Contract):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)


class Variable(Contract):
    reference: str = Field(min_length=1, max_length=1024)
    project_id: str
    environment: str
    service_id: str | None = None
    name: str
    key_id: str
    readable: bool = False
    environment_name: str | None = None
    service_name: str | None = None


class Deployment(Contract):
    id: str
    state: Literal["pending", "ready", "failed"]
    url: str | None = None
