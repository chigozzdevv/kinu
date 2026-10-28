import re
from typing import Annotated, Any, Literal

from contracts import Contract
from core.errors import ResourceConflictError
from fastapi import APIRouter, Depends
from integrations.catalog import CATALOG, integration
from pydantic import Field, SecretStr

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


class IntegrationRequest(Contract):
    operation: Literal["list", "configure"]
    integration_id: str | None = None
    values: dict[str, SecretStr] = Field(default_factory=dict, max_length=5)


@router.post("/integrations")
async def manage(
    body: IntegrationRequest, service: Annotated[Runtime, Depends(authenticated)]
) -> Any:
    if body.operation == "list":
        result = []
        for item in CATALOG:
            saved = await service.vault.integration(item.id)
            result.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "authorization": item.authorization,
                    "fields": item.fields,
                    "configured": all(saved.get(field) for field in item.fields),
                    "callback_url": service.config.public_url + f"/connections/{item.id}/callback"
                    if item.fields
                    else None,
                }
            )
        return result
    if not body.integration_id:
        raise ResourceConflictError("Choose an integration")
    definition = integration(body.integration_id)
    if not body.values.keys() <= set(definition.fields):
        raise ResourceConflictError("Unknown integration setting")
    saved = await service.vault.integration(definition.id)
    for key, value in body.values.items():
        raw = value.get_secret_value().strip()
        if len(raw) > 8192:
            raise ResourceConflictError("Integration setting is too long")
        if raw:
            saved[key] = raw
    if not all(saved.get(key) for key in definition.fields):
        raise ResourceConflictError("Complete the integration settings")
    if definition.id == "gmail" and not saved["client_id"].endswith(".apps.googleusercontent.com"):
        raise ResourceConflictError("Enter a Google OAuth client ID")
    if definition.id == "vercel" and not re.fullmatch(r"[a-z0-9-]{1,80}", saved["slug"]):
        raise ResourceConflictError("Enter the Vercel integration slug")
    await service.vault.configure(definition.id, saved)
    return {"configured": True}
