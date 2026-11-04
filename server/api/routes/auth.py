import re
from datetime import UTC, datetime
from typing import Annotated, cast

from contracts import Contract
from core.auth.browser import BrowserAuth, return_path
from core.auth.forms import Attempt
from core.errors import AuthenticationError, AuthorizationError
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, Field, SecretStr, StringConstraints

from api.deps import browser_session, required, services, session_cookie

router = APIRouter(prefix="/auth", tags=["auth"])
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    ),
]
Password = Annotated[SecretStr, Field(min_length=1, max_length=256)]


def _code(value: SecretStr) -> SecretStr:
    if not re.fullmatch(r"[0-9]{6}", value.get_secret_value()):
        raise ValueError("Enter the six-digit code")
    return value


Code = Annotated[SecretStr, Field(min_length=6, max_length=6), AfterValidator(_code)]


class EmailRequest(Contract):
    email: Email


class SignInRequest(EmailRequest):
    password: Password
    return_to: str = Field(default="/dashboard", max_length=2048)


class SignUpRequest(EmailRequest):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    password: Password


class VerifyRequest(EmailRequest):
    code: Code


class ResetRequest(VerifyRequest):
    password: Password


def _browser(request: Request) -> BrowserAuth:
    return required(cast(BrowserAuth | None, request.app.state.browser), "browser authentication")


async def _guard(request: Request, email: str, action: Attempt) -> BrowserAuth:
    browser = _browser(request)
    await browser.guard(
        session_cookie(request, browser.options.form_cookie) or "",
        request.headers.get("origin"),
        request.headers.get("x-csrf-token"),
        email,
        request.client.host if request.client else "unknown",
        action,
    )
    return browser


@router.get("/status")
async def status(request: Request) -> dict[str, bool]:
    return {"configured": request.app.state.browser is not None}


@router.get("/form")
async def form(request: Request, response: Response) -> dict[str, str]:
    browser = _browser(request)
    token, current = await browser.prepare(session_cookie(request, browser.options.form_cookie))
    response.set_cookie(
        browser.options.form_cookie,
        token,
        max_age=max(1, int((current.expires_at - datetime.now(UTC)).total_seconds())),
        secure=browser.options.secure,
        httponly=True,
        samesite=browser.options.same_site,
        path="/",
    )
    return {"csrf_token": current.csrf_token}


@router.post("/signin")
async def signin(body: SignInRequest, request: Request, response: Response) -> dict[str, str]:
    browser = await _guard(request, body.email, "signin")
    destination = return_path(body.return_to)
    token, current, step = await browser.signin(
        body.email,
        body.password,
        session_cookie(request, browser.options.form_cookie) or "",
        request.headers["x-csrf-token"],
        session_cookie(request, browser.options.cookie_name),
    )
    if token is not None and current is not None:
        response.set_cookie(
            browser.options.cookie_name,
            token,
            max_age=max(1, int((current.expires_at - datetime.now(UTC)).total_seconds())),
            secure=browser.options.secure,
            httponly=True,
            samesite=browser.options.same_site,
            path="/",
        )
        response.delete_cookie(
            browser.options.form_cookie,
            secure=browser.options.secure,
            httponly=True,
            samesite=browser.options.same_site,
            path="/",
        )
    return {"next": step, "return_to": destination}


@router.post("/signup", status_code=202)
async def signup(body: SignUpRequest, request: Request) -> dict[str, str]:
    browser = await _guard(request, body.email, "email")
    confirmed = await browser.passwords.signup(body.email, body.name, body.password)
    return {"next": "signin" if confirmed else "verify"}


@router.post("/verify")
async def verify(body: VerifyRequest, request: Request) -> dict[str, str]:
    browser = await _guard(request, body.email, "code")
    await browser.passwords.verify(body.email, body.code)
    return {"next": "signin"}


@router.post("/resend", status_code=202)
async def resend(body: EmailRequest, request: Request) -> dict[str, str]:
    browser = await _guard(request, body.email, "email")
    await browser.passwords.resend(body.email)
    return {"next": "verify"}


@router.post("/forgot", status_code=202)
async def forgot(body: EmailRequest, request: Request) -> dict[str, str]:
    browser = await _guard(request, body.email, "email")
    await browser.passwords.forgot(body.email)
    return {"next": "reset"}


@router.post("/reset")
async def reset(body: ResetRequest, request: Request, response: Response) -> dict[str, str]:
    browser = await _guard(request, body.email, "code")
    await browser.passwords.reset(body.email, body.code, body.password)
    await browser.store.revoke(body.email, browser.options.issuer)
    response.delete_cookie(
        browser.options.cookie_name,
        secure=browser.options.secure,
        httponly=True,
        samesite=browser.options.same_site,
        path="/",
    )
    return {"next": "signin"}


@router.post("/check", status_code=204)
async def check(request: Request) -> None:
    await browser_session(request)


@router.get("/session")
async def session(request: Request) -> JSONResponse:
    current = await browser_session(request)
    identity = current.identity
    workspace = None
    workspace_id = request.headers.get("x-kinu-workspace")
    if workspace_id:
        memberships = await required(services(request).accounts, "accounts").session(identity)
        membership = next(
            (item for item in memberships.organisations if item.organisation.id == workspace_id),
            None,
        )
        if membership is None:
            raise AuthorizationError("workspace membership is required")
        workspace = {"id": membership.organisation.id, "role": membership.role.value}
    return JSONResponse(
        {
            "identity": {
                "uid": identity.subject,
                "email": identity.email,
                "emailVerified": identity.email_verified,
                "displayName": identity.display_name,
            },
            "workspace": workspace,
            "csrf_token": current.csrf_token,
            "expires_at": current.expires_at.isoformat(),
        }
    )


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    browser = _browser(request)
    browser.require_origin(request.headers.get("origin"))
    token = session_cookie(request, browser.options.cookie_name)
    if token:
        try:
            current = await browser.store.get(token)
        except AuthenticationError:
            current = None
        if current is not None:
            browser.require_csrf(
                current, request.headers.get("origin"), request.headers.get("x-csrf-token")
            )
        await browser.store.delete(token)
    response = JSONResponse({"logout_url": browser.options.origin + "/auth"})
    response.delete_cookie(
        browser.options.cookie_name,
        secure=browser.options.secure,
        httponly=True,
        samesite=browser.options.same_site,
        path="/",
    )
    return response
