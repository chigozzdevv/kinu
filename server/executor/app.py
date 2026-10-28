import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from connectors.aws import AwsClient
from core.connection.control import ControlRequest, control
from core.errors import KinuError, ResourceNotFoundError, ResourceSetupRequiredError
from fastapi import Depends, FastAPI, HTTPException, Request
from mail.control import ControlRequest as MailRequest
from mail.control import control as mail_control

from executor.config import ExecutorSettings
from executor.runtime import Runtime, runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = ExecutorSettings()
    async with runtime(config) as service:
        app.state.service = service
        yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


async def authenticated(request: Request) -> Runtime:
    service: Runtime = request.app.state.service
    assert service.config.executor_token is not None
    expected = "Bearer " + service.config.executor_token.get_secret_value()
    value = request.headers.get("authorization", "")
    if not value.isascii() or not secrets.compare_digest(expected, value):
        raise HTTPException(401)
    if len(await request.body()) > 65536:
        raise HTTPException(413)
    return service


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/connections")
async def connections(
    body: ControlRequest, service: Annotated[Runtime, Depends(authenticated)]
) -> Any:
    return await control(service.database, AwsClient(), body)


@app.post("/mail")
async def mail(body: MailRequest, service: Annotated[Runtime, Depends(authenticated)]) -> Any:
    configured = await service.mail()
    if body.operation in {"begin", "finish", "senders"} and not configured.google.config.ready:
        raise ResourceSetupRequiredError("Gmail setup is incomplete")
    return await mail_control(configured, body)


@app.exception_handler(KinuError)
async def rejected(request: Request, error: KinuError) -> Any:
    from fastapi.responses import JSONResponse

    status = (
        404
        if isinstance(error, ResourceNotFoundError)
        else 503
        if isinstance(error, ResourceSetupRequiredError)
        else 409
    )
    return JSONResponse({"message": str(error)}, status_code=status)
