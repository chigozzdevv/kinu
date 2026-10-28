from dataclasses import dataclass

from connectors.hosting.models import Variable
from contracts import (
    Connection,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    CredentialGeneration,
    ManagedCredential,
)
from core.errors import ResourceConflictError


@dataclass(frozen=True)
class Target:
    binding: ConsumerBinding
    runtime: Connection
    service: ConsumerService
    variable: Variable | None = None


@dataclass(frozen=True)
class Snapshot:
    credential: ManagedCredential
    targets: tuple[Target, ...]
    current: CredentialGeneration
    controls: ControlVersion
    store: Connection
    provider: Connection

    @property
    def single(self) -> Target:
        if len(self.targets) != 1:
            raise ResourceConflictError("This executor requires one consumer")
        return self.targets[0]
