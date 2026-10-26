from datetime import UTC, datetime
from typing import cast

from core.auth.browser import BrowserAuth
from core.errors import AuthenticationError, AuthorizationError
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from api.deps import browser_session, required, services, session_cookie

router = APIRouter(prefix="/auth", tags=["auth"])


def _browser(request: Request) -> BrowserAuth:
    return required(cast(BrowserAuth | None, request.app.state.browser), "browser authentication")


@router.get("/status")
async def status(request: Request) -> dict[str, bool]:
    return {"configured": request.app.state.browser is not None}


@router.get("/login")
async def login(request: Request, return_to: str = "/dashboard") -> RedirectResponse:
    browser = _browser(request)
    url, binding = await browser.begin(return_to)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        browser.options.login_cookie,
        binding,
        max_age=600,
        secure=browser.options.secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    browser = _browser(request)
    binding = session_cookie(request, browser.options.login_cookie) or ""
    previous = session_cookie(request, browser.options.cookie_name)
    try:
        if error:
            await browser.store.consume(state, binding)
            raise AuthenticationError("sign-in was not completed")
        token, session, return_path = await browser.complete(code, state, binding, previous)
    except AuthenticationError:
        response = RedirectResponse(f"{browser.options.origin}/auth?error=signin", status_code=303)
    else:
        response = RedirectResponse(f"{browser.options.origin}{return_path}", status_code=303)
        response.set_cookie(
            browser.options.cookie_name,
            token,
            max_age=max(1, int((session.expires_at - datetime.now(UTC)).total_seconds())),
            secure=browser.options.secure,
            httponly=True,
            samesite="lax",
            path="/",
        )
    response.delete_cookie(
        browser.options.login_cookie,
        secure=browser.options.secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


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
    if request.headers.get("origin") != browser.options.origin:
        raise AuthorizationError("request origin is invalid")
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
    response = JSONResponse({"logout_url": browser.logout_url})
    response.delete_cookie(
        browser.options.cookie_name,
        secure=browser.options.secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response
