from datetime import datetime

from contracts import (
    Application,
    Connection,
    ConsumerService,
    Environment,
    ManagedCredential,
    RuntimeResourceMetadata,
)

from core.errors import ResourceConflictError


def _tenant(actual: str, expected: str) -> None:
    if actual != expected:
        raise ResourceConflictError("inventory relationship crosses organisation boundaries")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResourceConflictError(f"metadata returned no {label}")
    return value


def _domain_pattern(value: str) -> bool:
    return bool(value) and "." in value and all(ch.isalnum() or ch in ".-*" for ch in value)


def _resource_covered(resource: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        resource == boundary or resource.startswith(boundary.rstrip("/") + "/")
        for boundary in boundaries
    )


def _runtime_binding_matches(
    resource: RuntimeResourceMetadata,
    secret_resource: str,
    secret_reference: str,
    variable_name: str,
    container_name: str | None,
) -> bool:
    version = secret_reference.rpartition("/versions/")[2]
    matches = tuple(
        binding
        for binding in resource.secret_bindings
        if binding.name == variable_name
        and binding.container == container_name
        and binding.version == version
        and (binding.secret == secret_resource)
    )
    return len(matches) == 1


def _domain_covered(domain: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        domain == boundary
        or (boundary.startswith("*.") and domain.endswith(boundary.removeprefix("*")))
        for boundary in boundaries
    )


def _editable[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    value: T, expected_revision: int, label: str
) -> None:
    if value.archived_at is not None:
        raise ResourceConflictError(f"archived {label}s cannot be edited")
    if value.revision != expected_revision:
        raise ResourceConflictError(
            f"{label} expected revision {expected_revision}, found {value.revision}"
        )


def _change[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    value: T,
    expected_revision: int,
    updated_at: datetime,
    **updates: object | None,
) -> T:
    _editable(value, expected_revision, value.__class__.__name__.lower())
    populated = {key: item for key, item in updates.items() if item is not None}
    if not populated:
        raise ResourceConflictError("edit does not change any fields")
    changed = value.model_copy(
        update={**populated, "updated_at": updated_at, "revision": expected_revision + 1}
    )
    return value.__class__.model_validate(changed.model_dump())


def _archive[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    value: T,
    expected_revision: int,
    archived_at: datetime,
    **updates: object,
) -> T:
    _editable(value, expected_revision, value.__class__.__name__.lower())
    changed = value.model_copy(
        update={
            **updates,
            "archived_at": archived_at,
            "updated_at": archived_at,
            "revision": expected_revision + 1,
        }
    )
    return value.__class__.model_validate(changed.model_dump())


def _active[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    values: tuple[T, ...],
) -> tuple[T, ...]:
    return tuple(value for value in values if value.archived_at is None)
