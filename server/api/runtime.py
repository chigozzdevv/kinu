from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime

import httpx
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AccessControl
from core.auth.browser import BrowserAuth
from core.auth.cognito import CognitoTokenVerifier
from core.auth.store import SessionStore
from core.config import Settings
from core.inventory import InventoryService
from core.inventory.store import InventoryStore
from core.storage.database import Database
from fastapi import FastAPI

from api.deps import ApiServices


@asynccontextmanager
async def runtime(
    app: FastAPI,
    config: Settings,
    services: ApiServices | None,
    browser: BrowserAuth | None,
) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient())
        if config.database_url:
            database = Database(config.database_url.get_secret_value())
            stack.push_async_callback(database.close)
            await database.open()
            accounts = AccountStore(database, _now)
            if services is None:
                app.state.services = ApiServices(
                    access=AccessControl(accounts),
                    accounts=AccountService(accounts, _now),
                    inventory=InventoryService(InventoryStore(database), _now),
                )
            if browser is None and config.browser:
                app.state.browser = BrowserAuth(
                    config.browser,
                    SessionStore(database, _now),
                    CognitoTokenVerifier(config.browser.cognito, http),
                    http,
                    _now,
                )
        try:
            yield
        finally:
            app.state.browser = browser
            app.state.services = services


def _now() -> datetime:
    return datetime.now(UTC)
