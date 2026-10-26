import pytest
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    Permission,
    PrincipalGrant,
    Role,
)
from core.errors import AuthorizationError

IDENTITY = AuthenticatedIdentity(
    subject="107777777777777777777", issuer="https://identity.example.com"
)


class AccessRepository:
    def __init__(self, grant: PrincipalGrant | None) -> None:
        self._grant = grant

    async def get(
        self, organisation_id: str, identity: AuthenticatedIdentity
    ) -> PrincipalGrant | None:
        if organisation_id != "org_one" or identity != IDENTITY:
            return None
        return self._grant


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_viewer_can_read_but_cannot_mutate() -> None:
    grant = PrincipalGrant(subject=IDENTITY.subject, roles=frozenset({Role.VIEWER}))
    access = AccessControl(AccessRepository(grant))
    await access.require(IDENTITY, "org_one", Permission.RUN_READ)
    await access.require(IDENTITY, "org_one", Permission.AGENT_READ)
    permissions = await access.permissions(IDENTITY, "org_one")
    assert Permission.RUN_READ in permissions
    assert Permission.NOTIFICATION_WRITE not in permissions
    with pytest.raises(AuthorizationError, match="run\\.write"):
        await access.require(IDENTITY, "org_one", Permission.RUN_WRITE)
    with pytest.raises(AuthorizationError, match="agent\\.write"):
        await access.require(IDENTITY, "org_one", Permission.AGENT_WRITE)


@pytest.mark.anyio
async def test_disabled_principal_has_no_access() -> None:
    grant = PrincipalGrant(
        subject=IDENTITY.subject, roles=frozenset({Role.ADMINISTRATOR}), enabled=False
    )
    with pytest.raises(AuthorizationError, match="not enabled"):
        await AccessControl(AccessRepository(grant)).require(
            IDENTITY, "org_one", Permission.RUN_WRITE
        )


def test_actor_identity_is_stable_and_contract_safe() -> None:
    assert IDENTITY.actor_id == IDENTITY.actor_id
    assert IDENTITY.actor_id.startswith("actor_")
    assert len(IDENTITY.actor_id) == 38
