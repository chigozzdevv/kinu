from dataclasses import dataclass

from contracts import (
    Connection,
    ConsumerBinding,
    ControlVersion,
    CredentialGeneration,
    ManagedCredential,
)


@dataclass(frozen=True)
class Snapshot:
    credential: ManagedCredential
    binding: ConsumerBinding
    current: CredentialGeneration
    controls: ControlVersion
    store: Connection
    runtime: Connection
    provider: Connection
