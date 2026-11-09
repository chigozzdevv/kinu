import hashlib
from dataclasses import dataclass


def actor_for(subject: str) -> str:
    value = hashlib.sha256(subject.encode()).hexdigest()[:32]
    return f"actor_{value}"


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    subject: str
    issuer: str
    email: str | None = None
    email_verified: bool = False
    display_name: str | None = None
    connected_via: str = "Identity provider"

    @property
    def actor_id(self) -> str:
        return actor_for(self.subject)

    @property
    def document_id(self) -> str:
        return hashlib.sha256(self.subject.encode()).hexdigest()
