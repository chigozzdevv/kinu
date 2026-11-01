from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from connectors.aws import AwsClient
from connectors.base.errors import ConnectorError
from core.connection.control import ControlRequest, control
from core.errors import KinuError, ResourceNotFoundError, ResourceSetupRequiredError
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mail.control import ControlRequest as MailRequest
from mail.control import control as mail_control

from executor.browser import router as browser_router
from executor.config import ExecutorSettings
from executor.deps import authenticated
from executor.hosts import router as hosts_router
from executor.integrations import router as integrations_router
from executor.playbooks import router as playbooks_router
from executor.providers import router as providers_router
from executor.rotations import router as rotations_router
from executor.runtime import Runtime, runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = ExecutorSettings()
    async with runtime(config) as service:
        app.state.service = service
        yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    try:
        service: Runtime = request.app.state.service
        await service.database.documents.command("ping")
        await service.vault.database.documents.command("ping")
        await service.queue.queue.client.ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})


@app.post("/connections")
async def connections(
    body: ControlRequest, service: Annotated[Runtime, Depends(authenticated)]
) -> Any:
    return await control(
        service.database, AwsClient(), body, service.authorizations, service.providers
    )


@app.post("/mail")
async def mail(body: MailRequest, service: Annotated[Runtime, Depends(authenticated)]) -> Any:
    configured = await service.mail()
    if body.operation in {"begin", "finish", "senders"} and not configured.google.config.ready:
        raise ResourceSetupRequiredError("Gmail setup is incomplete")
    return await mail_control(configured, body)


@app.exception_handler(KinuError)
async def rejected(request: Request, error: KinuError) -> Any:
    status = (
        404
        if isinstance(error, ResourceNotFoundError)
        else 503
        if isinstance(error, ResourceSetupRequiredError)
        else 409
    )
    return JSONResponse({"message": str(error)}, status_code=status)


@app.exception_handler(ConnectorError)
async def connection_failed(request: Request, error: ConnectorError) -> JSONResponse:
    return JSONResponse({"message": "Connection check failed"}, status_code=502)


@app.exception_handler(RequestValidationError)
async def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse({"message": "Check the connection settings"}, status_code=422)


app.include_router(integrations_router)
app.include_router(providers_router)
app.include_router(hosts_router)
app.include_router(browser_router)
app.include_router(rotations_router)
app.include_router(playbooks_router)
