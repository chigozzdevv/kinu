import asyncio
import base64
import json
from typing import cast

from core.auth import Permission
from core.auth.browser import BrowserAuth
from core.config import Settings
from core.errors import KinuError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/browser-gateway/v1/setup/live")
async def setup(websocket: WebSocket) -> None:
    config = cast(Settings, websocket.app.state.config)
    browser = cast(BrowserAuth | None, websocket.app.state.browser)
    api = websocket.app.state.services
    name = "__Host-kinu" if config.public_url.startswith("https://") else "kinu.session"
    cookie = websocket.cookies.get(name, "")
    if (
        websocket.headers.get("origin") != config.public_url
        or not browser
        or not cookie
        or not api
        or not api.private
    ):
        await websocket.close(code=4401)
        return
    try:
        initial = await browser.store.get(cookie)
        await websocket.accept()
        text = await asyncio.wait_for(websocket.receive_text(), 15)
        if len(text.encode()) > 4096:
            raise ValueError
        hello = json.loads(text)
        org, identity, token = hello["organisation_id"], hello["setup_id"], hello["token"]
        if not all(
            isinstance(value, str) and 1 <= len(value) <= 256 for value in (org, identity, token)
        ):
            raise ValueError
        async with asyncio.timeout(600):
            while True:
                current = await browser.store.get(cookie)
                if current.identity.subject != initial.identity.subject:
                    raise ValueError
                await api.access.require(current.identity, org, Permission.INVENTORY_WRITE)
                text = await asyncio.wait_for(websocket.receive_text(), 30)
                if len(text.encode()) > 8192:
                    raise ValueError
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise ValueError
                result = await api.private.call(
                    "/browser",
                    {
                        "operation": "stream",
                        "organisation_id": org,
                        "setup_id": identity,
                        "subject": current.identity.subject,
                        "token": token,
                        "data": data,
                    },
                )
                if result.get("type") == "frame":
                    await websocket.send_bytes(base64.b64decode(result["image"], validate=True))
                else:
                    await websocket.send_json(result)
    except WebSocketDisconnect:
        return
    except (KinuError, ValueError, KeyError, TypeError, TimeoutError):
        await websocket.close(code=4403)
