from importlib import import_module
from typing import TYPE_CHECKING, Any

from telemetry.redact import REDACTED, redact

if TYPE_CHECKING:
    from telemetry.runtime import TelemetryConfig, instrument, operation, record

_RUNTIME_EXPORTS = frozenset({"TelemetryConfig", "instrument", "operation", "record"})


def __getattr__(name: str) -> Any:
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module("telemetry.runtime"), name)


__all__ = ["REDACTED", "TelemetryConfig", "instrument", "operation", "record", "redact"]
