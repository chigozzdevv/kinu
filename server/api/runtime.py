from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime

import httpx
from agents.memory import AgentMemoryService
from agents.store import AgentStore
from connectors.aws import AwsClient
from core.account import AccountService
from core.account.store import AccountStore
from core.approval import ApprovalService
from core.approval.store import ApprovalStore
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.auth import AccessControl
from core.auth.browser import BrowserAuth
from core.auth.cognito import CognitoTokenVerifier
from core.auth.forms import FormStore
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
from core.storage.factory import application_database
from core.storage.history import HistoryStore
from core.storage.notification import NotificationStore
from core.storage.runs import RunStore
from core.verification import ProbeService
from core.verification.store import ProbeStore
from core.workflow import RunWorkflow
from demo.mail import Source
from demo.queue import Queue
from fastapi import FastAPI
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
            accounts = AccountStore(database, _now)
            if services is None:
                inventory = InventoryStore(database)
                workflow = RunWorkflow(RunStore(database))
                counts = OverviewStore(database)
                agents = AgentStore(database)
                notifications = NotificationService(NotificationStore(database), _now)
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
                bridge = (
                    ConnectionBridge(AwsClient(), config.aws_region, config.connector_function)
                    if config.connector_function and config.connection_principals
                    else None
                )
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
                    if bridge
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
                    dispatcher=Dispatcher(
                        database, AwsClient(), config.aws_region, config.rotation_queue_url, queue
                    ),
                    demo=Source(database, queue) if queue else None,
                    gmail=GmailBridge(AwsClient(), config.aws_region, config.gmail_function)
                    if config.gmail_function
                    else None,
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
            if browser is None and config.browser:
                app.state.browser = BrowserAuth(
                    config.browser,
                    SessionStore(database, _now),
                    FormStore(database, _now),
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
