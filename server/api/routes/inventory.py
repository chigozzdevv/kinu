from fastapi import APIRouter

from api.inventory import applications, connections, credentials, setup

router = APIRouter(prefix="/v1/organisations/{organisation_id}/inventory", tags=["inventory"])
for module in (applications, connections, credentials, setup):
    router.include_router(module.router)
