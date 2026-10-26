from dataclasses import dataclass
from typing import Any


class SecretValue:
    def __init__(self, value: bytes | bytearray) -> None:
        self._buffer = bytearray(value)
        self._closed = False

    def bytes(self) -> bytes:
        if self._closed:
            raise RuntimeError("secret buffer has been cleared")
        return bytes(self._buffer)

    def clear(self) -> None:
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._closed = True

    def __enter__(self) -> "SecretValue":
        return self

    def __exit__(self, *_: object) -> None:
        self.clear()

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"


@dataclass(frozen=True, slots=True)
class ConnectorResponse:
    result: dict[str, Any]
    evidence: tuple[tuple[str, bytes, str], ...] = ()
    secret: SecretValue | None = None
