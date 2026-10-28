from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time

import httpx
from agents.native import NativeAgent
from connectors.aws import AwsClient
from connectors.aws.secrets import AwsSecrets
from connectors.http import HttpProviderConnector
from core.connection.roles import ConnectionRoles
from core.connection.secrets import ConnectionSecrets
from core.inventory.store import InventoryStore
from core.private import PrivateClient
from core.storage.database import Database
from hosting.mapping import Mappings
from hosting.service import Service as Hosts
from jobs.queue import Jobs
from mail.assessment import Assessment
from mail.google import Google
from mail.models import GoogleConfig
from mail.poll import Poller
from mail.service import Service
from mail.store import Store
from mail.vault import Vault as MailVault
from notification.worker import Worker as EmailWorker
from rotation.effects import Effects
from rotation.hosting import Effects as HostEffects
from rotation.queue import Dispatcher
from rotation.rotate import Rotation
from rotation.router import Router
from rotation.snapshot import loader

from executor.authorization import Authorization
from executor.config import ExecutorSettings
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
        self.mappings = Mappings(
            self.hosts, self.versions, HttpProviderConnector(self.authorizations)
        )
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
        self.dispatcher = Dispatcher(database, queue)
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
            yield service
    finally:
        await queue.close()
        await private.close()
        await database.close()
