import httpx
import pytest
from core.auth import AuthenticatedIdentity
from core.errors import ActiveRunConflictError, ResourceConflictError
from core.storage.database import Database
from hosting.lifecycle import Lifecycle
from hosting.locks import acquire
from rotation.snapshot import loader
from tests.storage.hosting.rotation.fixture import Platform
from tests.storage.hosting.rotation.test import setup

pytestmark = pytest.mark.anyio


async def test_workload_lock_blocks_other_rotations_and_connection_replacement(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, _, _, credential = await setup(database, identity, http)
        org = credential.organisation_id
        state = await loader(database)(org, credential.id, credential.control_version)
        await acquire(database, state, "run_one")
        await acquire(database, state, "run_one")
        with pytest.raises(ActiveRunConflictError):
            await acquire(database, state, "run_other")
        host = state.targets[0].runtime
        await database.collection("run_locks").insert_one(
            {"organisation_id": org, "credential_id": credential.id, "run_id": "run_one"}
        )
        with pytest.raises(ResourceConflictError, match="Finish this account"):
            await Lifecycle(database, hosts.grants).pause(org, host.id, host.revision)
        assert await hosts.inventory.get_connection(org, host.id) == host
