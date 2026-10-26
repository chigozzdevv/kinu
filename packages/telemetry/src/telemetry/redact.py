import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
SAFE_KEYS = frozenset({"credential-id", "generation-id", "secret-reference"})
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "code",
        "cookie",
        "credential",
        "password",
        "private",
        "secret",
        "session",
        "state",
        "token",
        "verifier",
    }
)


def redact(value: Any) -> Any:
    if isinstance(value, bytes | bytearray):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _sensitive(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact(item) for item in value]
    if isinstance(value, str) and _sensitive_value(value):
        return REDACTED
    return value


def _sensitive(key: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", key)
    normalised = re.sub(r"[^a-z0-9]+", "-", separated.lower()).strip("-")
    if normalised in SAFE_KEYS:
        return False
    return normalised == "api-key" or bool(SENSITIVE_KEYS.intersection(normalised.split("-")))


def _sensitive_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_VALUES)


_SENSITIVE_VALUES = (
    re.compile(r"(?i)^\s*(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}\s*$"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{40,}|gh[pousr]_[A-Za-z0-9]{36,})\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)^[a-z][a-z0-9+.-]*://[^/@:\s]+:[^/@\s]+@"),
    re.compile(
        r"(?i)[?&#](?:access_token|api_key|authorization|code|password|secret|signature|token)="
    ),
)
