from datetime import UTC, datetime

import httpx
from connectors.resend.client import Resend
from connectors.resend.config import definition
from contracts import Connection
from core.auth import AuthenticatedIdentity
from core.inventory.store import InventoryStore
from core.storage.database import Database
from executor.authorization import Authorization
from executor.receipts import Receipts
from executor.vault import Vault
from executor.versions import Versions
from hosting.mapping import Mappings
from hosting.models import Begin, Selection
from hosting.proof import Proof
from hosting.seed import Seed
from hosting.service import Service
from pydantic import SecretStr
from tests.storage.hosting.test import workspace
from tests.storage.resend.fixture import DOMAIN


async def setup(
    database: Database, identity: AuthenticatedIdentity, http: httpx.AsyncClient
) -> tuple[str, str, Seed]:
    org = await workspace(database, identity)
    vault = Vault(database, SecretStr("private-resend-sandbox-at-least-thirty-two-characters"))
    await vault.initialize()
    versions = Versions(vault)
    await versions.initialize()
    receipts = Receipts(vault)
    await receipts.initialize()
    provider = Resend(Authorization(vault, versions), receipts, http)
    hosts = Service(database, vault, http, "http://localhost:8080")
    await hosts.grants.initialize()
    draft = await hosts.begin(
        org,
        identity.actor_id,
        "a" * 64,
        Begin(
            platform="render",
            display_name="Render",
            token=SecretStr("host-management-fixture"),
        ),
    )
    host = await hosts.save(
        org, draft["connection_id"], identity.actor_id, "a" * 64, Selection(project_ids=("app",))
    )
    now = datetime.now(UTC)
    source = Connection.model_validate(
        {
            "id": "provider_resend",
            "organisation_id": org,
            "platform": "resend",
            "display_name": "Resend",
            "roles": ["provider"],
            "interface": "api",
            "authorization": "api-key",
            "authorization_reference": "private://provider_resend",
            "capabilities": list(Resend.tools),
            "allowed_resources": ["resend:credentials:*"],
            "http": definition(DOMAIN),
            "status": "ready",
            "region": "global",
            "created_at": now,
            "updated_at": now,
        }
    )
    await vault.put(org, source.id, {"token": "management-fixture"})
    await InventoryStore(database).add_connection(source)
    return org, host.id, Seed(Mappings(hosts, versions, provider), provider, Proof(http))
