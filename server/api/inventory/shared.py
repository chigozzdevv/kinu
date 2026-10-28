from uuid import uuid4

from browser.setup import BrowserSetupApi
from core.errors import ResourceConflictError

from api.deps import ApiServices, required


def _organisation(actual: str, expected: str) -> None:
    if actual != expected:
        raise ResourceConflictError("request body crosses organisation boundary")


def _setup(api: ApiServices) -> BrowserSetupApi:
    return required(api.browser_setup, "browser setup")


async def _audit_change(
    api: ApiServices,
    organisation_id: str,
    actor_id: str,
    kind: str,
    resource_id: str,
    revision: int,
) -> None:
    if api.audit is None:
        return
    await api.audit.append(
        f"audit_{uuid4().hex}",
        organisation_id,
        kind,
        actor_id,
        f"inventory/{resource_id}",
        {"revision": revision},
    )
