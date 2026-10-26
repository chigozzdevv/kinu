from collections.abc import Mapping
from typing import Any

from telemetry import REDACTED
from telemetry import redact as redact_sensitive

_PLAINTEXT_SECRET_KEYS = frozenset({"plaintext", "private-key", "secret-value"})


def redact(value: Any, key: str = "") -> Any:
    normalised = key.lower().replace("_", "-")
    if normalised in _PLAINTEXT_SECRET_KEYS:
        return REDACTED
    if isinstance(value, Mapping):
        return redact_sensitive(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, bytes):
        return REDACTED
    return value
