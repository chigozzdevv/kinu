import re
from typing import Any

from contracts import CredentialGeneration, ManagedCredential, PlaybookStep, RotationRun


def browser_step_context(
    run: RotationRun,
    credential: ManagedCredential,
    old: CredentialGeneration | None,
    target: CredentialGeneration | None,
) -> dict[str, Any]:
    suffix = run.id.removeprefix("run_")[:24]
    provider = re.sub(r"[^a-z0-9]+", "-", credential.provider.lower()).strip("-")
    return {
        "run_id": run.id,
        "credential_id": run.credential_id,
        "replacement_provider_id": f"{provider or 'credential'}-{suffix}",
        "replacement_provider_display_name": f"{provider or 'credential'}-{suffix}",
        "target_generation_id": run.target_generation_id,
        "target_provider_id": target.provider_id if target else None,
        "target_secret_reference": target.secret_reference if target else None,
        "old_generation_id": run.current_generation_id,
        "old_provider_id": old.provider_id if old else None,
        "old_provider_path": old.provider_id.lstrip("/") if old and old.provider_id else None,
        "old_provider_display_name": old.provider_display_name if old else None,
        "old_secret_reference": old.secret_reference if old else None,
    }


def resolve_playbook_step(
    step: PlaybookStep,
    context: dict[str, Any],
    *,
    protected: bool | None = None,
) -> PlaybookStep:
    resolved = resolve_playbook_value(step.model_dump(mode="python"), context)
    if not isinstance(resolved, dict):
        raise ValueError("resolved playbook step is invalid")
    if protected is not None:
        resolved["protected"] = protected
    return PlaybookStep.model_validate(resolved)


def resolve_playbook_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        exact = _PLACEHOLDER.fullmatch(value)
        if exact is not None:
            return _required(context, exact.group(1))

        def replace(match: re.Match[str]) -> str:
            resolved = _required(context, match.group(1))
            if not isinstance(resolved, str):
                raise ValueError(f"playbook variable {match.group(1)} is not text")
            return resolved

        return _PLACEHOLDER.sub(replace, value)
    if isinstance(value, tuple | list):
        return [resolve_playbook_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: resolve_playbook_value(item, context) for key, item in value.items()}
    return value


def _required(context: dict[str, Any], key: str) -> Any:
    if key not in context or context[key] is None:
        raise ValueError(f"playbook variable {key} is unavailable")
    return context[key]


_PLACEHOLDER = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")
