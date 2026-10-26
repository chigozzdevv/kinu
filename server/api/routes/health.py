from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    configured = request.app.state.services is not None and request.app.state.browser is not None
    return JSONResponse(
        status_code=200 if configured else 503,
        content={"status": "ready" if configured else "unconfigured"},
    )
