from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime

import httpx
from connectors.aws import AwsClient
from core.account import AccountService
from core.account.store import AccountStore
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.auth import AccessControl
from core.auth.browser import BrowserAuth
from core.auth.cognito import CognitoTokenVerifier
from core.auth.store import SessionStore
from core.config import Settings
from core.incident.service import IncidentService
from core.incident.store import IncidentStore
from core.inventory import InventoryService
from core.inventory.store import InventoryStore
from core.storage.factory import application_database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from demo.journal import RunHistory
from demo.mail import Source
from demo.queue import Queue
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
        if config.has_storage:
            database = application_database(config)
            stack.push_async_callback(database.close)
            await database.open()
            accounts = AccountStore(database, _now)
            if services is None:
                inventory = InventoryStore(database)
                workflow = RunWorkflow(RunStore(database))
                audit = AuditWriter(AuditStore(database), config.aws_region, _now)
                queue = (
                    Queue(
                        AwsClient(),
                        config.aws_region,
                        config.demo_queue_url,
                        config.demo_organisation,
                    )
                    if config.demo_queue_url and config.demo_organisation
                    else None
                )
                app.state.services = ApiServices(
                    access=AccessControl(accounts),
                    accounts=AccountService(accounts, _now),
                    inventory=InventoryService(inventory, _now),
                    workflow=workflow,
                    history=RunHistory(database),
                    dispatcher=queue,
                    demo=Source(database, queue) if queue else None,
                    audit=audit,
                    incidents=IncidentService(
                        IncidentStore(database), _now, inventory, workflow, audit=audit
                    ),
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
