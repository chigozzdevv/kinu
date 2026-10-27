import hmac
import os
import re
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

from connectors.aws import AwsApi, AwsClient
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from demo import view
from demo.auth import identity, session, signature
from demo.store import ProviderStore

COOKIE = "__Host-kinu-demo"


def create_app(aws: AwsApi | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    client = aws or AwsClient()
    region = os.environ.get("AWS_REGION", "eu-west-2")
    table = os.environ.get("KINU_DEMO_TABLE", "")
    secret = os.environ.get("KINU_DEMO_SEED", "")
    origin = os.environ.get("KINU_DEMO_ORIGIN", "")

    async def store(request: Request) -> ProviderStore:
        result = await client.call("secretsmanager", "get_secret_value", region, SecretId=secret)
        request.state.seed = str(result["SecretString"]).encode()
        return ProviderStore(client, region, table, request.state.seed)

    async def admin(request: Request) -> tuple[ProviderStore, str, str]:
        repository = await store(request)
        cookie = request.cookies.get(COOKIE, "")
        org = identity(request.state.seed, cookie)
        if not org:
            raise HTTPException(401, "Demo administrator sign-in is required")
        return repository, org, signature(request.state.seed, "csrf", cookie)

    async def form(request: Request) -> dict[str, str]:
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Demo form origin is invalid")
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(413, "Demo form is too large")
        return {
            key: values[0]
            for key, values in parse_qs(body.decode(), max_num_fields=5).items()
            if len(values) == 1
        }

    async def protected(request: Request) -> tuple[ProviderStore, str, dict[str, str]]:
        repository, org, csrf = await admin(request)
        values = await form(request)
        if not hmac.compare_digest(values.get("csrf", ""), csrf):
            raise HTTPException(403, "Demo form token is invalid")
        return repository, org, values

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        if not request.cookies.get(COOKIE):
            return HTMLResponse(view.login())
        repository, org, csrf = await admin(request)
        return HTMLResponse(view.keys(org, await repository.list(org), csrf))

    @app.post("/login")
    async def sign_in(request: Request) -> RedirectResponse:
        values = await form(request)
        await store(request)
        org = values.get("organisation_id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,127}", org) or not hmac.compare_digest(
            values.get("password", ""), signature(request.state.seed, "admin", org)
        ):
            raise HTTPException(401, "Demo sign-in failed")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE,
            session(request.state.seed, org),
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=600,
            path="/",
        )
        return response

    @app.post("/keys", response_class=HTMLResponse)
    async def create(request: Request) -> HTMLResponse:
        repository, org, values = await protected(request)
        request_id = values.get("request_id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,127}", request_id):
            raise HTTPException(422, "Demo creation request is invalid")
        created = await repository.create(org, request_id)
        csrf = signature(request.state.seed, "csrf", request.cookies[COOKIE])
        return HTMLResponse(view.keys(org, await repository.list(org), csrf, created))

    @app.post("/keys/{key_id}/revoke")
    async def revoke(key_id: str, request: Request) -> RedirectResponse:
        repository, org, _ = await protected(request)
        if not re.fullmatch(r"[0-9a-f]{24}", key_id):
            raise HTTPException(422, "Demo key identity is invalid")
        await repository.revoke(org, key_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/api/keys")
    async def metadata(request: Request) -> JSONResponse:
        repository, org, _ = await admin(request)
        return JSONResponse({"keys": await repository.list(org), "demo": True})

    @app.get("/api/check")
    async def check(request: Request) -> JSONResponse:
        repository = await store(request)
        header = request.headers.get("authorization", "")
        org = request.headers.get("x-demo-organisation", "")
        if not header.startswith("Bearer ") or not re.fullmatch(r"[a-z][a-z0-9_-]{2,127}", org):
            raise HTTPException(401, "Demo credential is required")
        key = await repository.check(org, header[7:])
        if key is None:
            raise HTTPException(401, "Demo credential was rejected")
        return JSONResponse(
            {"ok": True, "provider_id": key["id"], "scopes": key["scopes"], "demo": True}
        )

    @app.middleware("http")
    async def headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            }
        )
        return response

    return app
