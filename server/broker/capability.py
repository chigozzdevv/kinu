import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from contracts import Stage
from core.errors import CapabilityError
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


@dataclass(frozen=True, slots=True)
class CapabilityClaims:
    organisation_id: str
    run_id: str
    agent_id: str
    tool: str
    connection_id: str
    stage: Stage
    fencing_token: int
    request_digest: str
    action_digest: str
    expires_at: int
    nonce: str
    approval_id: str | None = None


class CapabilitySigner:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("capability signing key must be a 32-byte Ed25519 private key")
        self._key = Ed25519PrivateKey.from_private_bytes(key)
        self._verifier = CapabilityVerifier(self.public_key)

    @property
    def public_key(self) -> bytes:
        return self._key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @property
    def encoded_public_key(self) -> str:
        return _urlsafe(self.public_key)

    def mint(self, claims: CapabilityClaims) -> str:
        header = _encode({"alg": "EdDSA", "typ": "FKC", "v": 2})
        payload = _encode({**asdict(claims), "stage": claims.stage.value})
        signature = _urlsafe(self._key.sign(f"{header}.{payload}".encode()))
        return f"{header}.{payload}.{signature}"

    def verify(self, token: str, now: datetime) -> CapabilityClaims:
        return self._verifier.verify(token, now)


class CapabilityVerifier:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("capability verification key must be a 32-byte Ed25519 public key")
        self._key = Ed25519PublicKey.from_public_bytes(key)

    @classmethod
    def decode(cls, value: str) -> "CapabilityVerifier":
        try:
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except ValueError as error:
            raise ValueError("capability public key is not valid base64url") from error
        return cls(key)

    def verify(self, token: str, now: datetime) -> CapabilityClaims:
        parts = token.split(".")
        if len(parts) != 3:
            raise CapabilityError("action capability is malformed")
        header, payload, signature = parts
        try:
            raw_signature = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            self._key.verify(raw_signature, f"{header}.{payload}".encode())
        except (InvalidSignature, ValueError) as error:
            raise CapabilityError("action capability signature is invalid") from error
        header_value = _decode(header)
        if header_value != {"alg": "EdDSA", "typ": "FKC", "v": 2}:
            raise CapabilityError("action capability header is unsupported")
        value = _decode(payload)
        try:
            claims = CapabilityClaims(
                organisation_id=_claim(value, "organisation_id", str),
                run_id=_claim(value, "run_id", str),
                agent_id=_claim(value, "agent_id", str),
                tool=_claim(value, "tool", str),
                connection_id=_claim(value, "connection_id", str),
                stage=Stage(_claim(value, "stage", str)),
                fencing_token=_claim(value, "fencing_token", int),
                request_digest=_claim(value, "request_digest", str),
                action_digest=_claim(value, "action_digest", str),
                expires_at=_claim(value, "expires_at", int),
                nonce=_claim(value, "nonce", str),
                approval_id=_optional_claim(value, "approval_id", str),
            )
        except (TypeError, ValueError) as error:
            raise CapabilityError("action capability claims are invalid") from error
        if claims.expires_at <= int(now.timestamp()):
            raise CapabilityError("action capability has expired")
        return claims


def request_digest(tool: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"payload": payload, "tool": tool},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _encode(value: dict[str, Any]) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return _urlsafe(payload)


def _decode(value: str) -> dict[str, Any]:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        result = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as error:
        raise CapabilityError("action capability encoding is invalid") from error
    if not isinstance(result, dict):
        raise CapabilityError("action capability body is invalid")
    return result


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _claim(value: dict[str, Any], key: str, kind: type[str] | type[int]) -> Any:
    result = value.get(key)
    if not isinstance(result, kind) or isinstance(result, bool):
        raise TypeError(f"claim {key} is invalid")
    return result


def _optional_claim(value: dict[str, Any], key: str, kind: type[str]) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, kind):
        raise TypeError(f"claim {key} is invalid")
    return result
