import hashlib
from dataclasses import dataclass


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
        value = hashlib.sha256(self.subject.encode()).hexdigest()[:32]
        return f"actor_{value}"

    @property
    def document_id(self) -> str:
        return hashlib.sha256(self.subject.encode()).hexdigest()
