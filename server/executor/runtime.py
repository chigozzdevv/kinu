import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from time import time

import httpx
from agents.native import NativeAgent
from browser.catalog import Catalog as BrowserCatalog
from browser.planner import Planner
from browser.setupserver import Setups
from browser.worker import Worker as BrowserWorker
from connectors.aws import AwsClient
from connectors.aws.secrets import AwsSecrets
from core.connection.roles import ConnectionRoles
from core.connection.secrets import ConnectionSecrets
from core.inventory.store import InventoryStore
from core.private import PrivateClient
from core.storage.database import Database
from hosting.mapping import Mappings
from hosting.provision import Provisioning
from hosting.service import Service as Hosts
from ingestion.runtime import RuntimeInspector
from ingestion.worker import Monitor
from jobs.queue import Jobs
from mail.assessment import Assessment
from mail.google import Google
from mail.models import GoogleConfig
from mail.poll import Poller
from mail.service import Service
from mail.store import Store
from mail.vault import Vault as MailVault
from notification.worker import Worker as EmailWorker
from rotation.approval import RevocationApprovals
from rotation.effects import Effects
from rotation.hosting import Effects as HostEffects
from rotation.queue import Dispatcher
from rotation.recovery import Recovery
from rotation.rotate import Rotation
from rotation.router import Router
from rotation.snapshot import loader

from executor.authorization import Authorization
from executor.config import ExecutorSettings
from executor.provider import Providers
from executor.reader import Reader
from executor.vault import Vault
from executor.versions import Versions


class Runtime:
    def __init__(
        self,
        config: ExecutorSettings,
        database: Database,
        vault: Vault,
        http: httpx.AsyncClient,
        queue: Jobs,
    ) -> None:
        self.config, self.database, self.vault, self.http, self.queue = (
            config,
            database,
            vault,
            http,
            queue,
        )
        assert config.native_token is not None
        self.agent = NativeAgent(PrivateClient(http, config.native_url, config.native_token))
        self.versions = Versions(vault)
        self.authorizations = Authorization(
            vault,
            ConnectionSecrets(
                InventoryStore(database),
                Reader(
                    self.versions,
                    AwsSecrets(
                        AwsClient(), ConnectionRoles(database, managed=True, read_only=True)
                    ),
                ),
            ),
        )
        self.hosts = Hosts(database, vault, http, config.public_url)
        self.browser_catalog = BrowserCatalog(database, vault)
        self.browser_setups = Setups(self.browser_catalog)
        self.browser_worker = BrowserWorker(
            self.browser_catalog, self.versions, Planner(self.agent)
        )
        self.providers = Providers(self.authorizations, self.browser_worker, self.versions)
        self.mappings = Mappings(self.hosts, self.versions, self.providers)
        self.provisioning = Provisioning(self.mappings, queue, region=config.aws_region)
        self.mail_store = Store(vault.database)
        self.rotation = Rotation(
            database,
            Router(
                HostEffects(self.hosts, self.versions, self.mappings.provider),
                Effects(AwsClient(), database, self.authorizations),
            ),
            loader(database),
            config.aws_region,
        )
        self.rotation.approvals = RevocationApprovals(
            database, vault, self.rotation.effects, self.rotation.audit
        )
        self.recovery = Recovery(database, self.rotation, queue, self.providers)
        self.dispatcher = Dispatcher(database, queue)
        self.monitor = Monitor(
            database,
            self.providers,
            RuntimeInspector(database, self.hosts, self.versions),
            self.dispatcher,
            config.aws_region,
        )
        self.email = EmailWorker(
            database, http, config.resend_key, config.email_sender, config.public_url
        )

    async def mail(self) -> Service:
        settings = await self.vault.integration("gmail")
        assert self.config.vault_key is not None
        config = GoogleConfig(**settings, key=self.config.vault_key)
        return Service(
            self.mail_store,
            Google(config, self.http),
            MailVault(config),
            self.database,
            self.config.public_url,
        )

    async def poll(self) -> int:
        if not await self.mail_store.due(int(time())):
            return 0
        service = await self.mail()
        if not service.google.config.ready:
            return 0
        return await Poller(
            service,
            Assessment(
                self.database, self.mail_store, self.agent, self.dispatcher, self.config.aws_region
            ),
        ).run()


@asynccontextmanager
async def runtime(config: ExecutorSettings) -> AsyncIterator[Runtime]:
    database = Database(config.mongo_url.get_secret_value(), config.mongo_database)
    private = Database(config.vault_url.get_secret_value(), config.vault_database)
    queue = Jobs(config.redis_url.get_secret_value())
    try:
        await database.open()
        await private.open()
        assert config.vault_key is not None
        vault = Vault(private, config.vault_key)
        await vault.initialize()
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as http:
            service = Runtime(config, database, vault, http, queue)
            await service.mail_store.initialize()
            await service.hosts.grants.initialize()
            await service.versions.initialize()
            await service.browser_worker.images.initialize()
            await service.providers.receipts.initialize()
            await service.provisioning.initialize()

            async def reap() -> None:
                while True:
                    await asyncio.sleep(30)
                    await service.browser_setups.reap()

            reaper = asyncio.create_task(reap())
            try:
                yield service
            finally:
                reaper.cancel()
                with suppress(asyncio.CancelledError):
                    await reaper
                await service.browser_setups.shutdown()
    finally:
        await queue.close()
        await private.close()
        await database.close()
