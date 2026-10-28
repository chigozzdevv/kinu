import base64
import hashlib
import json
import secrets
from typing import Any

from core.errors import ResourceConflictError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mail.models import GoogleConfig


class Vault:
    def __init__(self, config: GoogleConfig) -> None:
        self._cipher = AESGCM(hashlib.sha256(config.key.get_secret_value().encode()).digest())

    def seal(self, value: dict[str, Any], context: str) -> str:
        nonce = secrets.token_bytes(12)
        payload = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(
            nonce + self._cipher.encrypt(nonce, payload, context.encode())
        ).decode()

    def open(self, value: str, context: str) -> dict[str, Any]:
        try:
            data = base64.urlsafe_b64decode(value)
            result = json.loads(self._cipher.decrypt(data[:12], data[12:], context.encode()))
            if not isinstance(result, dict):
                raise ValueError("invalid grant")
            return result
        except Exception:
            raise ResourceConflictError("Gmail authorization could not be opened") from None
