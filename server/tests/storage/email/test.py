import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from contracts import MemberRole, NotificationKind, Severity
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.errors import LeaseConflictError
from core.notification import (
    EmailDeliveryConfiguration,
    NotificationDispatcher,
    NotificationService,
)
from core.storage.database import Database
from core.storage.deliveries import Deliveries
from core.storage.notification import NotificationStore
from emailer.notification import REFERENCE, Mailer
from emailer.receipts import Receipts
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


async def setup(
    database: Database, identity: AuthenticatedIdentity
) -> tuple[str, NotificationService, AccountService]:
    accounts = AccountService(
        AccountStore(database, lambda: datetime.now(UTC)), lambda: datetime.now(UTC)
    )
    org = (await accounts.create_organisation(identity, "Mail & <alerts>")).organisation.id
    notifications = NotificationService(
        NotificationStore(database),
        lambda: datetime.now(UTC),
        EmailDeliveryConfiguration(REFERENCE, "Kinu <alerts@example.com>"),
    )
    return org, notifications, accounts


async def emit(service: NotificationService, org: str, event: str = "event_one") -> None:
    await service.emit(
        event,
        org,
        NotificationKind.ROTATION_FAILED,
        Severity.HIGH,
        "Key <rotation> failed",
        "Review <evidence>.",
        "/dashboard/rotations",
        "run_one",
    )


async def test_outbox_is_atomic_and_retries_use_the_same_provider_key_and_payload(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org, notifications, _ = await setup(database, identity)
    await notifications.register_email_endpoint(
        "endpoint_one",
        org,
        identity.actor_id,
        "owner@example.com",
        frozenset({NotificationKind.ROTATION_FAILED}),
    )
    await asyncio.gather(*(emit(notifications, org) for _ in range(3)))
    assert await database.collection("notification_deliveries").count_documents({}) == 1
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url == "https://api.resend.com/emails/receipt-one"
            return httpx.Response(200, json={"last_event": "delivered"})
        calls.append(request)
        assert request.url == "https://api.resend.com/emails"
        assert request.headers["authorization"] == "Bearer private-mail-test"
        body = json.loads(request.content)
        assert body["to"] == ["owner@example.com"]
        assert "&lt;evidence&gt;" in body["html"] and "<evidence>" in body["text"]
        return (
            httpx.Response(429, text="private-provider-diagnostic")
            if len(calls) == 1
            else httpx.Response(200, json={"id": "receipt-one"})
        )

    store = Deliveries(database)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        dispatcher = NotificationDispatcher(
            store,
            Mailer(
                database,
                http,
                SecretStr("private-mail-test"),
                "Kinu <alerts@example.com>",
                "https://kinu.example",
            ),
            "email_one",
            lambda: datetime.now(UTC),
        )
        assert (await dispatcher.drain()).failed == 1
        first = await store.collection.find_one({"organisation_id": org})
        assert (
            first
            and first["state"] == "pending"
            and "private-provider-diagnostic" not in str(first)
        )
        await store.collection.update_one(
            {"_id": first["_id"]}, {"$set": {"available_at": datetime.now(UTC)}}
        )
        assert (await dispatcher.drain()).sent == 1
        assert calls[0].headers["idempotency-key"] == calls[1].headers["idempotency-key"]
        assert calls[0].content == calls[1].content
        assert (await dispatcher.drain()).claimed == 0
        saved = await store.collection.find_one({"_id": first["_id"]})
        assert saved and saved["provider_receipt"] == "receipt-one"
        assert saved["provider_status"] == "accepted"
        await store.collection.update_one(
            {"_id": first["_id"]}, {"$set": {"status_due": datetime.now(UTC)}}
        )
        await Receipts(database, http, SecretStr("private-mail-test")).poll()
        delivered = await store.collection.find_one({"_id": first["_id"]})
        assert (
            delivered
            and delivered["provider_status"] == "delivered"
            and "status_due" not in delivered
        )


async def test_invites_only_reach_their_recipient_and_revoked_invites_are_not_sent(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org, notifications, accounts = await setup(database, identity)
    invitations = []
    for email in ("one@example.com", "two@example.com"):
        await notifications.register_invitation_endpoint(org, email)
        invitation = await accounts.invite(org, identity, email, MemberRole.VIEWER)
        invitations.append(invitation)
        await notifications.emit(
            "invite_" + invitation.id,
            org,
            NotificationKind.TEAM_INVITATION,
            Severity.LOW,
            "Join workspace",
            "Sign in to join.",
            "/auth",
            invitation.id,
        )
    assert await database.collection("notification_deliveries").count_documents({}) == 2
    await accounts.revoke_invitation(org, invitations[0].id, invitations[0].revision)
    sent = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["to"])
        return httpx.Response(200, json={"id": "receipt-two"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        dispatcher = NotificationDispatcher(
            Deliveries(database),
            Mailer(
                database, http, SecretStr("private"), "alerts@example.com", "https://kinu.example"
            ),
            "email_one",
            lambda: datetime.now(UTC),
        )
        result = await dispatcher.drain()
        assert result.sent == 1 and result.failed == 1 and sent == [["two@example.com"]]


async def test_expired_leases_are_fenced_and_disabled_members_receive_no_email(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org, notifications, _ = await setup(database, identity)
    await notifications.register_email_endpoint(
        "endpoint_one",
        org,
        identity.actor_id,
        "owner@example.com",
        frozenset({NotificationKind.ROTATION_FAILED}),
    )
    await emit(notifications, org)
    store, now = Deliveries(database), datetime.now(UTC)
    first = (await store.claim("owner_one", now, timedelta(seconds=1), 1))[0]
    second = (await store.claim("owner_two", now + timedelta(seconds=2), timedelta(seconds=60), 1))[
        0
    ]
    assert first.delivery.started_at == second.delivery.started_at
    with pytest.raises(LeaseConflictError):
        await store.mark_sent(first, "owner_one", "receipt", now)
    await store.mark_failed(second, "owner_two", "retry", now, False)
    await database.collection("members").update_one(
        {"organisation_id": org}, {"$set": {"data.enabled": False}}
    )

    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Disabled member must not receive email")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        dispatcher = NotificationDispatcher(
            store,
            Mailer(
                database, http, SecretStr("private"), "alerts@example.com", "https://kinu.example"
            ),
            "owner_three",
            lambda: datetime.now(UTC),
        )
        assert (await dispatcher.drain()).failed == 1
