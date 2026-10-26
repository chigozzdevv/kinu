from collections.abc import Awaitable, Callable

from core.errors import (
    ActiveRunConflictError,
    AuthenticationError,
    AuthorizationError,
    IdempotencyConflictError,
    IntegrationNotConfiguredError,
    KinuError,
    LeaseConflictError,
    PlaybookError,
    ResourceConflictError,
    ResourceNotFoundError,
    ResourceSetupRequiredError,
    RevisionConflictError,
    RunNotFoundError,
    StorageIntegrityError,
    TransitionRejectedError,
)
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from policy import PolicyViolationError
from telemetry import instrument

from api.deps import ApiServices
from api.routes import (
    agents_router,
    approvals_router,
    audit_router,
    browsers_router,
    github_router,
    health_router,
    incidents_router,
    inventory_router,
    notifications_router,
    overview_router,
    playbooks_router,
    probes_router,
    runs_router,
    session_router,
    settings_router,
    walkthroughs_router,
)

ErrorHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def create_app(services: ApiServices | None = None) -> FastAPI:
    app = FastAPI(title="Kinu", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.services = services
    app.include_router(health_router)
    app.include_router(session_router)
    app.include_router(github_router)
    app.include_router(runs_router)
    app.include_router(inventory_router)
    app.include_router(notifications_router)
    app.include_router(playbooks_router)
    app.include_router(probes_router)
    app.include_router(approvals_router)
    app.include_router(agents_router)
    app.include_router(browsers_router)
    app.include_router(incidents_router)
    app.include_router(walkthroughs_router)
    app.include_router(audit_router)
    app.include_router(overview_router)
    app.include_router(settings_router)
    app.add_exception_handler(KinuError, _kinu_error)
    app.add_exception_handler(PolicyViolationError, _controls_error)
    instrument(app, "kinu-api")
    return app


async def _kinu_error(request: Request, error: Exception) -> JSONResponse:
    del request
    if isinstance(error, IntegrationNotConfiguredError):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "integration-not-configured", str(error))
    if isinstance(error, AuthenticationError):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated", str(error))
    if isinstance(error, AuthorizationError):
        return _error(status.HTTP_403_FORBIDDEN, "forbidden", str(error))
    if isinstance(error, RunNotFoundError | ResourceNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, "not-found", str(error))
    if isinstance(error, ResourceSetupRequiredError):
        return _error(status.HTTP_409_CONFLICT, "setup-required", str(error))
    if isinstance(
        error,
        (
            ActiveRunConflictError,
            IdempotencyConflictError,
            LeaseConflictError,
            RevisionConflictError,
            ResourceConflictError,
        ),
    ):
        return _error(status.HTTP_409_CONFLICT, "conflict", str(error))
    if isinstance(error, TransitionRejectedError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "transition-rejected", str(error))
    if isinstance(error, PlaybookError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "playbook-rejected", str(error))
    if isinstance(error, StorageIntegrityError):
        return _error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "storage-integrity-failure",
            "stored run data failed an integrity check",
        )
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal-error",
        "an internal Kinu error occurred",
    )


async def _controls_error(request: Request, error: Exception) -> JSONResponse:
    del request
    return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "controls-rejected", str(error))


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message})
