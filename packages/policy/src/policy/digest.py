import hashlib
import json
from base64 import b64encode
from collections.abc import Mapping, Set
from enum import Enum
from typing import Any

from contracts import Contract
from pydantic_core import to_jsonable_python


def digest(contract: Contract) -> str:
    payload = _canonical(contract.model_dump(mode="python", exclude_none=True))
    canonical = _json(payload)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key.value if isinstance(raw_key, Enum) else raw_key)
            if key in output:
                raise ValueError(f"canonical digest contains duplicate key {key!r}")
            output[key] = _canonical(item)
        return output
    if isinstance(value, Set):
        return sorted((_canonical(item) for item in value), key=_json)
    if isinstance(value, tuple | list):
        return [_canonical(item) for item in value]
    if isinstance(value, bytes):
        return {"$bytes": b64encode(value).decode("ascii")}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return _canonical(to_jsonable_python(value))


def _json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
