import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api.app import create_app
from contracts import NotificationKind, Severity
from core.account import AccountService
from core.account.service import principal_id
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity, PrincipalGrant, Role
from core.auth.identity import actor_for
from core.config import Settings
from core.errors import ResourceConflictError, ResourceNotFoundError, RevisionConflictError
from core.notification import EmailDeliveryConfiguration, NotificationService
from core.storage.database import Database
from core.storage.notification import NotificationStore
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 27, tzinfo=UTC)


async def workspace(database: Database, identity: AuthenticatedIdentity) -> str:
    membership = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "Notifications")
    return membership.organisation.id


async def test_notifications_survive_recomposition_and_concurrent_delivery(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    service = NotificationService(NotificationStore(database), lambda: NOW)
    results = await asyncio.gather(
        *(
            service.emit(
                "event_one",
                org,
                NotificationKind.INCIDENT,
                Severity.HIGH,
                "Credential exposed",
                "Review the incident.",
                "/dashboard/incidents",
                "incident_one",
                incident_id="incident_one",
            )
            for _ in range(3)
        )
    )
    assert sum(applied for _, applied in results) == 1
    first = results[0][0]
    store = NotificationStore(database)
    assert await store.list_notifications(org, 10) == (first,)
    assert await store.list_notifications("org_other", 10) == ()
    with pytest.raises(ResourceNotFoundError):
        await store.mark_read("org_other", first.id, 0, NOW)
    changed = await store.mark_read(org, first.id, 0, NOW)
    assert changed.read_at == NOW and changed.revision == 1
    with pytest.raises(RevisionConflictError):
        await store.mark_read(org, first.id, 0, NOW)
    assert await store.mark_read(org, first.id, 1, NOW) == changed


async def test_endpoints_are_durable_principal_scoped_and_revision_checked(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    service = NotificationService(
        NotificationStore(database),
        lambda: NOW,
        EmailDeliveryConfiguration("secret://email/access/versions/one", "alerts@example.com"),
    )
    first = await service.register_email_endpoint(
        "endpoint_one",
        org,
        "actor_one",
        "owner@example.com",
        frozenset({NotificationKind.INCIDENT}),
    )
    await service.register_email_endpoint(
        "endpoint_two",
        org,
        "actor_two",
        "other@example.com",
        frozenset({NotificationKind.INCIDENT}),
    )
    second = NotificationService(NotificationStore(database), lambda: NOW)
    assert await second.list_email_endpoints(org, "actor_one") == (first,)
    assert await second.list_email_endpoints("org_other", "actor_one") == ()
    with pytest.raises(ResourceNotFoundError):
        await second.set_email_endpoint_enabled(org, "actor_two", first.id, 0, False)
    changed = await second.set_email_endpoint_enabled(org, "actor_one", first.id, 0, False)
    assert not changed.enabled and changed.revision == 1
    with pytest.raises(RevisionConflictError):
        await second.set_email_endpoint_enabled(org, "actor_one", first.id, 0, True)
    assert (
        await NotificationStore(database).register_endpoint(
            first.model_copy(update={"created_at": NOW + timedelta(seconds=1)})
        )
        == changed
    )
    with pytest.raises(ResourceConflictError):
        await NotificationStore(database).register_endpoint(
            first.model_copy(update={"recipients": ("someone@example.com",)})
        )


async def test_default_runtime_serves_notification_settings_without_email_delivery(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await workspace(database, identity)
    monkeypatch.setattr("api.runtime.application_database", lambda config: database)
    app = create_app(
        browser=make_browser(identity),
        settings=Settings(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        root = f"/v1/organisations/{org}/notifications"
        for suffix in ("", "/endpoints", "/topics", "/settings"):
            response = await client.get(root + suffix, headers=HEADERS)
            assert response.status_code == 200, response.text
            if suffix == "/settings":
                assert response.json() == {"email_enabled": False}
            elif suffix != "/topics":
                assert response.json() == []
        assert (await client.get(root + "/settings")).status_code == 401
        assert (
            await client.get("/v1/organisations/org_other/notifications/settings", headers=HEADERS)
        ).status_code == 403
        response = await client.post(
            root + "/endpoints",
            headers=HEADERS,
            json={
                "id": "endpoint_one",
                "email_address": "owner@example.com",
                "topics": ["incidents"],
            },
        )
        assert response.status_code == 409
        assert response.json()["message"] == "email notifications are not configured"


async def test_disabling_a_member_discards_only_their_email_endpoints(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    subject = "member_two"
    await database.collection("members").insert_one(
        {
            "organisation_id": org,
            "subject": subject,
            "id": principal_id(subject),
            "data": PrincipalGrant(
                subject=subject,
                roles=frozenset({Role.VIEWER}),
                email="member-two@example.com",
                created_at=NOW,
                updated_at=NOW,
            ).model_dump(mode="json"),
        }
    )
    service = NotificationService(
        NotificationStore(database),
        lambda: NOW,
        EmailDeliveryConfiguration("secret://email/access/versions/one", "alerts@example.com"),
    )
    await service.register_email_endpoint(
        "endpoint_member",
        org,
        actor_for(subject),
        "member-two@example.com",
        frozenset({NotificationKind.INCIDENT}),
    )
    kept = await service.register_email_endpoint(
        "endpoint_admin",
        org,
        identity.actor_id,
        "owner@example.com",
        frozenset({NotificationKind.INCIDENT}),
    )
    changed = await AccountStore(database, lambda: NOW).update_member(
        org, principal_id(subject), 0, Role.VIEWER, False, NOW
    )
    assert not changed.enabled
    assert await NotificationStore(database).list_endpoints(org) == (kept,)
