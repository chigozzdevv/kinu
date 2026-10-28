from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime

import httpx
from agents.memory import AgentMemoryService
from agents.store import AgentStore
from core.account import AccountService
from core.account.installation import Installation
from core.account.store import AccountStore
from core.approval import ApprovalService
from core.approval.store import ApprovalStore
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.auth import AccessControl
from core.auth.browser import BrowserAuth
from core.auth.forms import FormStore
from core.auth.password import PasswordClient
from core.auth.store import SessionStore
from core.config import Settings
from core.connection.bridge import ConnectionBridge, RuntimeMetadata, SecretMetadata
from core.connection.service import ConnectionService
from core.incident.service import IncidentService
from core.incident.store import IncidentStore
from core.inventory import InventoryService
from core.inventory.store import InventoryStore
from core.notification import NotificationService
from core.overview import OverviewService
from core.overview.store import OverviewStore
from core.playbook import PlaybookService
from core.playbook.store import PlaybookStore
from core.private import PrivateClient
from core.storage.factory import application_database
from core.storage.history import HistoryStore
from core.storage.notification import NotificationStore
from core.storage.runs import RunStore
from core.verification import ProbeService
from core.verification.store import ProbeStore
from core.workflow import RunWorkflow
from emailer.resend import Resend
from fastapi import FastAPI
from jobs.queue import Jobs
from mail.bridge import Bridge as GmailBridge
from rotation.queue import Dispatcher

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
            app.state.database = database
            accounts = AccountStore(database, _now)
            if services is None:
                inventory = InventoryStore(database)
                workflow = RunWorkflow(RunStore(database))
                counts = OverviewStore(database)
                agents = AgentStore(database)
                notifications = NotificationService(NotificationStore(database), _now)
                audit = AuditWriter(AuditStore(database), config.aws_region, _now)
                queue = Jobs(config.redis_url.get_secret_value()) if config.executor_token else None
                if queue:
                    stack.push_async_callback(queue.close)
                private = (
                    PrivateClient(http, config.executor_url, config.executor_token)
                    if config.executor_token
                    else None
                )
                bridge = ConnectionBridge(private) if private else None
                app.state.services = ApiServices(
                    access=AccessControl(accounts),
                    accounts=AccountService(accounts, _now),
                    inventory=InventoryService(
                        inventory,
                        _now,
                        provider_metadata=bridge,
                        credential_verifier=bridge,
                        secret_metadata=SecretMetadata(bridge) if bridge else None,
                        runtime_metadata=RuntimeMetadata(bridge) if bridge else None,
                    ),
                    connections=ConnectionService(
                        database,
                        bridge,
                        tuple((config.connection_principals or "").split(",")),
                        _now,
                    )
                    if bridge and config.connection_principals
                    else None,
                    workflow=workflow,
                    playbooks=PlaybookService(PlaybookStore(database), _now, inventory),
                    approvals=ApprovalService(ApprovalStore(database), _now, notifications, audit),
                    probes=ProbeService(ProbeStore(database), _now),
                    overview=OverviewService(counts, counts, counts, counts, _now),
                    agent_repository=agents,
                    agent_continuity=AgentMemoryService(agents, _now),
                    history=HistoryStore(database),
                    notifications=notifications,
                    dispatcher=Dispatcher(database, queue),
                    gmail=GmailBridge(private) if private else None,
                    private=private,
                    installation=Installation(database),
                    audit=audit,
                    incidents=IncidentService(
                        IncidentStore(database),
                        _now,
                        inventory,
                        workflow,
                        notifier=notifications,
                        audit=audit,
                    ),
                )
            if browser is None and config.browser and config.auth_secret:
                app.state.browser = BrowserAuth(
                    config.browser,
                    SessionStore(database, _now),
                    FormStore(database, _now),
                    PasswordClient(
                        database,
                        Resend(http, config.resend_key, config.email_sender),
                        config.browser.issuer,
                        config.auth_secret,
                        _now,
                    ),
                    _now,
                )
        try:
            yield
        finally:
            app.state.database = None
            app.state.browser = browser
            app.state.services = services


def _now() -> datetime:
    return datetime.now(UTC)
