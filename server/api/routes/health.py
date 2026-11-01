import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    configured = request.app.state.services is not None and request.app.state.browser is not None
    description = "ready" if configured else "unconfigured"
    database = getattr(request.app.state, "database", None)
    if configured and database is not None:
        try:
            async with asyncio.timeout(3):
                await database.documents.command("ping")
                queue = getattr(request.app.state, "queue", None)
                if queue and not await queue.queue.client.get("kinu:worker:heartbeat"):
                    configured = False
                    description = "worker-unavailable"
        except Exception:
            configured = False
            description = "unavailable"
    return JSONResponse(
        status_code=200 if configured else 503,
        content={"status": description},
    )
