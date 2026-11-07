from collections.abc import Awaitable, Callable

from core.auth.browser import BrowserAuth
from core.auth.forms import AttemptLimitError, FormError
from core.config import Settings
from core.errors import (
    ActiveRunConflictError,
    ApprovalError,
    AuthenticationError,
    AuthorizationError,
    IdempotencyConflictError,
    IdentityUnavailableError,
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
    StorageUnavailableError,
    TransitionRejectedError,
)
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from policy import PolicyViolationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send
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
from api.routes.api_playbooks import router as api_playbooks_router
from api.routes.auth import router as auth_router
from api.routes.connections import router as connections_router
from api.routes.demo import router as demo_router
from api.routes.features import router as features_router
from api.routes.gateway import router as gateway_router
from api.routes.gmail import router as gmail_router
from api.routes.hosts import router as hosts_router
from api.routes.installation import router as installation_router
from api.routes.integrations import router as integrations_router
from api.routes.providers import router as providers_router
from api.routes.sources import router as sources_router
from api.runtime import runtime

ErrorHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def create_app(
    services: ApiServices | None = None,
    *,
    browser: BrowserAuth | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    config = settings or Settings()

    app = FastAPI(
        title="Kinu",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lambda app: runtime(app, config, services, browser),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.public_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Kinu-Workspace", "Idempotency-Key"],
    )
    app.state.services = services
    app.state.browser = browser
    app.state.config = config
    app.include_router(auth_router)
    app.include_router(integrations_router)
    app.include_router(installation_router)
    app.include_router(providers_router)
    app.include_router(health_router)
    app.include_router(session_router)
    app.include_router(github_router)
    app.include_router(runs_router)
    app.include_router(inventory_router)
    app.include_router(connections_router)
    app.include_router(gmail_router)
    app.include_router(hosts_router)
    app.include_router(gateway_router)
    app.include_router(notifications_router)
    app.include_router(playbooks_router)
    app.include_router(api_playbooks_router)
    app.include_router(probes_router)
    app.include_router(approvals_router)
    app.include_router(agents_router)
    app.include_router(browsers_router)
    app.include_router(incidents_router)
    app.include_router(walkthroughs_router)
    app.include_router(audit_router)
    app.include_router(overview_router)
    app.include_router(settings_router)
    app.include_router(demo_router)
    app.include_router(features_router)
    app.include_router(sources_router)
    app.add_exception_handler(KinuError, _kinu_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(PolicyViolationError, _controls_error)
    app.add_middleware(BodyLimitMiddleware)
    app.middleware("http")(_private_responses)
    instrument(app, "kinu-api")
    return app


_BODY_LIMIT = 1_048_576
_AUTH_BODY_LIMIT = 4096


def _body_limit(method: str, path: str) -> int | None:
    if method == "POST" and path.startswith("/auth/"):
        return _AUTH_BODY_LIMIT
    if method in {"POST", "PUT", "PATCH"} and path.startswith("/v1/"):
        return _BODY_LIMIT
    return None


class BodyLimitMiddleware:
    """Caps request bodies by streaming the receive channel instead of buffering blindly."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = _body_limit(scope["method"], scope["path"]) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await _error(413, "invalid-input", "The request body is too large.")(
                    scope, receive, send
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


async def _private_responses(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    if request.url.path.startswith(("/auth/", "/v1/")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def _kinu_error(request: Request, error: Exception) -> JSONResponse:
    del request
    if isinstance(error, FormError):
        return _error(error.status, "invalid-input", str(error))
    if isinstance(error, AttemptLimitError):
        response = _error(429, "attempt-limit", str(error))
        response.headers["Retry-After"] = str(error.retry_after)
        return response
    if isinstance(error, IntegrationNotConfiguredError):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "integration-not-configured", str(error))
    if isinstance(error, IdentityUnavailableError):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "identity-unavailable", str(error))
    if isinstance(error, StorageUnavailableError):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage-unavailable", str(error))
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
            ApprovalError,
            IdempotencyConflictError,
            LeaseConflictError,
            RevisionConflictError,
            ResourceConflictError,
        ),
    ):
        return _error(status.HTTP_409_CONFLICT, "conflict", str(error))
    if isinstance(error, TransitionRejectedError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "transition-rejected", str(error))
    if isinstance(error, (PlaybookError, ApprovalError)):
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
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message},
        headers={"Cache-Control": "no-store"},
    )


async def _validation_error(request: Request, error: Exception) -> JSONResponse:
    if request.url.path.startswith("/auth/"):
        return _error(422, "invalid-input", "Check the form fields and try again.")
    return _error(422, "invalid-input", "Check the supplied fields and try again.")
