from typing import Any, Literal

State = Literal["full_access", "sending_access", "rejected", "unavailable"]


def backoff(value: str | None) -> float:
    try:
        seconds = float(value or "1")
    except ValueError:
        return 1
    return min(3, max(1, seconds))


def classify(status: int, payload: Any) -> State:
    if not isinstance(payload, dict):
        return "unavailable"
    if status == 200 and payload.get("object") == "list" and isinstance(payload.get("data"), list):
        return "full_access"
    name, message = payload.get("name"), payload.get("message")
    if (
        status == 401
        and name == "restricted_api_key"
        and message == "This API key is restricted to only send emails."
    ):
        return "sending_access"
    if (
        status in {400, 401} and name == "validation_error" and message == "API key is invalid"
    ) or (
        status == 403
        and (
            (name == "restricted_api_key" and message == "API key is not active")
            or name == "suspended_api_key"
        )
    ):
        return "rejected"
    return "unavailable"
