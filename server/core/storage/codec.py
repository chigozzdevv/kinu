from collections.abc import Mapping
from enum import Enum
from typing import Any

from contracts import Contract


def encode(contract: Contract) -> dict[str, Any]:
    value = normalise(contract.model_dump(mode="python"))
    if not isinstance(value, dict):
        raise TypeError("a storage contract must encode to a mapping")
    return value


def normalise(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): normalise(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [normalise(item) for item in value]
    return value
