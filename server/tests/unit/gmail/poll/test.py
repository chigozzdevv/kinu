import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cloud import mail as cloud
from core.errors import ResourceConflictError
from core.storage.database import Database
from mail.assessment import Assessment
from mail.google import Google
from mail.models import GoogleConfig, Grant, Mailbox, Sender
from mail.poll import Poller
from mail.service import Service
from mail.vault import Vault
from pydantic import SecretStr
from tests.mail import MemoryMail

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Receiver(Assessment):
    def __init__(self, store: MemoryMail) -> None:
        self.store = store
        self.calls = 0

    async def active(self, box: Mailbox) -> None:
        current, _ = await self.store.mailbox(box.organisation_id, box.id)
        if current.status != "ready" or current.revision != box.revision:
            raise ResourceConflictError("disconnected")

    async def prepare(
        self, box: Mailbox, identity: str, raw: bytes, sender: Sender, received: datetime
    ) -> dict[str, Any]:
        await self.active(box)
        return {"done": False, "revision": box.revision}

    async def process(self, box: Mailbox, identity: str, data: dict[str, Any]) -> None:
        if not data["done"]:
            await self.active(box)
            self.calls += 1
            await self.store.message(box.organisation_id, box.id, identity, data | {"done": True})


@pytest.mark.parametrize("disconnect", [False, True])
async def test_refresh_deduplication_and_disconnect_during_delivery(disconnect: bool) -> None:
    now = datetime.now(UTC)
    store = MemoryMail()
    box = Mailbox(
        id="gmail_one",
        organisation_id="org_one",
        email="owner@gmail.com",
        connected_at=now,
        status="ready",
        senders=(
            Sender(sender="security@provider.example", provider_connection_id="provider_one"),
        ),
    )
    config = GoogleConfig(
        client_id="kinu.apps.googleusercontent.com",
        client_secret=SecretStr("fixture"),
        key=SecretStr("x" * 64),
    )
    reads, refreshes = [], []
    raw = b"From: security@provider.example\r\nSubject: Alert\r\n\r\nKey exposed"

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            refreshes.append(request)
            return httpx.Response(200, json={"access_token": "renewed", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer renewed"
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "message_one"}]})
        if request.url.params["format"] == "metadata":
            return httpx.Response(
                200,
                json={
                    "internalDate": str(int((now - timedelta(seconds=10)).timestamp()) * 1000),
                    "payload": {
                        "headers": [{"name": "From", "value": "security@provider.example"}]
                    },
                    "sizeEstimate": len(raw),
                },
            )
        reads.append(request)
        if disconnect:
            await store.save(box.model_copy(update={"status": "disabled", "revision": 1}), None, 0)
        return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(raw).decode()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = Service(
            store,
            Google(config, client),
            Vault(config),
            Database("postgresql://unused"),
            "https://kinu.example",
        )
        grant = Grant(
            access_token=SecretStr("old"),
            refresh_token=SecretStr("fixture-refresh"),
            expires_at=now - timedelta(seconds=1),
        )
        await store.save(
            box,
            service.seal_grant(box, grant),
            None,
            cursor={"since": int(now.timestamp()) - 100, "start": int(now.timestamp()) - 100},
        )
        receiver = Receiver(store)
        poller = Poller(service, receiver)
        await poller.run()
        await poller.run()
    assert len(reads) == len(refreshes) == 1
    assert receiver.calls == (0 if disconnect else 1)
    _, saved = await store.mailbox(box.organisation_id, box.id)
    assert "fixture-refresh" not in str(saved)
    if disconnect:
        assert saved["grant"] is None and not saved["enabled"]
    else:
        assert service.open_grant(box, saved["grant"]).access_token.get_secret_value() == "renewed"


async def test_empty_scheduled_check_does_not_read_secrets_or_open_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class Aws:
        async def call(
            self, service: str, operation: str, region: str, **values: Any
        ) -> dict[str, Any]:
            calls.append((service, operation))
            assert (service, operation) == ("dynamodb", "query")
            return {"Items": []}

    monkeypatch.setattr(cloud, "AwsClient", Aws)
    monkeypatch.setenv("KINU_GMAIL_TABLE", "fixture-table")
    assert await cloud.execute({"kind": "poll"}) == {"processed": 0}
    assert calls == [("dynamodb", "query")]
