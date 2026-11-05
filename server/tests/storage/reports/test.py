import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from contracts import (
    Connection,
    ConnectionStatus,
    ExposureSource,
    ManagedCredential,
)
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.records import Records
from demo.config import DemoConfig
from policy import digest
from tests.browser import HEADERS
from tests.storage.rotation.conftest import demo as demo
from tests.storage.sources.test import email_signature
from tests.storage.sources.test import source_app as source_app

pytestmark = pytest.mark.anyio


@pytest.fixture
async def report_env(
    source_app: Any, demo: DemoConfig, database: Database, monkeypatch: pytest.MonkeyPatch
) -> Any:
    service, org = source_app.service, demo.organisation_id
    inbox = await service.sources.forwarding.create(org)
    await service.sources.forwarding.received(inbox, None)
    queued = []

    async def scope(organisation_id: str, credential_id: str) -> None:
        assert organisation_id == org and credential_id == demo.credential_id

    async def submit(organisation_id: str, run_id: str) -> None:
        assert organisation_id == org
        queued.append(run_id)

    monkeypatch.setattr(service.dispatcher, "require_scope", scope)
    monkeypatch.setattr(service.dispatcher, "submit", submit)
    return SimpleNamespace(
        client=source_app.client,
        service=service,
        org=org,
        box=inbox,
        queued=queued,
        credential_id=demo.credential_id,
        path=f"/v1/organisations/{org}/incidents",
        other=source_app.two,
    )


async def controls(database: Database, env: Any, *, automatic: bool, linked: bool = True) -> None:
    inventory = InventoryStore(database)
    credential = await inventory.get_credential(env.org, env.credential_id)
    previous = await inventory.get_control_version(
        env.org, credential.id, credential.control_version
    )
    definition = previous.definition.model_copy(
        update={
            "automatic_triggers": frozenset({"credential-exposure-detected", "credential-expiring"})
            if automatic
            else frozenset({"credential-runtime-drift"}),
            "exposure_sources": (
                ExposureSource(connection_id=env.box.id, resource=env.box.address),
            )
            if linked
            else (),
        }
    )
    version = previous.model_copy(
        update={
            "id": "controls_" + uuid4().hex,
            "number": previous.number + 1,
            "definition": definition,
            "digest": digest(definition),
        }
    )
    async with Records(database).write(env.org) as rows:
        await rows.add(version, env.org)
        await rows.replace(
            credential.model_copy(
                update={
                    "control_version": version.id,
                    "revision": credential.revision + 1,
                }
            ),
            credential.revision,
        )


def payload(env: Any) -> dict[str, Any]:
    return {
        "credential_id": env.credential_id,
        "kind": "exposure",
        "details": "Forwarded test alert",
        "simulate": True,
        "source_connection_id": env.box.id,
        "recipient": "owner@example.com",
    }


def headers() -> dict[str, str]:
    return HEADERS | {"Idempotency-Key": str(uuid4())}


@pytest.mark.parametrize("automatic,linked", [(True, True), (False, True), (True, False)])
async def test_email_simulation_uses_forwarding_and_key_policy_once(
    report_env: Any,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    automatic: bool,
    linked: bool,
) -> None:
    env = report_env
    await controls(database, env, automatic=automatic, linked=linked)
    options = await env.client.get(
        env.path + "/report-options", params={"credential_id": env.credential_id}, headers=HEADERS
    )
    assert options.status_code == 200, options.text
    assert options.json()["sources"][0]["linked"] is linked
    assert options.json()["recipient"] and options.json()["email_sender"]
    assert "Credential:" in options.json()["preview"] and "Apps:" in options.json()["preview"]
    sent = []

    def send(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.resend.com/emails"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": str(uuid4())})

    request_headers = headers()
    body = payload(env)
    response = await env.client.post(env.path + "/reports", json=body, headers=request_headers)
    assert response.status_code == 202, response.text
    report = response.json()
    assert report["state"] == "queued" and report["incident"] is None
    assert "ticket" not in response.text and "receiving-fixture-key" not in response.text
    assert await database.collection("runs").count_documents({}) == 0
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as http:
        monkeypatch.setattr(env.service, "http", http)
        await env.service.sources.drain()
    assert len(sent) == 1 and sent[0]["to"] == ["owner@example.com"]
    assert "Kinu test reference:" in sent[0]["text"]
    assert "a" * 24 in sent[0]["text"]
    assert "kinu://vault" not in sent[0]["text"]

    incoming_id = str(uuid4())

    async def received(self: Any, identity: str) -> dict[str, Any]:
        return {"id": identity, **sent[0], "received_for": [env.box.address]}

    monkeypatch.setattr("mail.receiving.Receiving.get", received)
    webhook = json.dumps({"type": "email.received", "data": {"email_id": incoming_id}}).encode()
    delivered = await env.client.post(
        "/v1/webhooks/resend", content=webhook, headers=email_signature(webhook)
    )
    assert delivered.status_code == 202
    await env.service.sources.drain()
    status = (await env.client.get(env.path + "/reports/" + report["id"], headers=HEADERS)).json()
    assert status["state"] == "completed" and status["incident"]["source"] == "test-email"
    assert status["incident"]["credential_id"] == env.credential_id
    assert status["incident"]["description"] == body["details"]
    assert bool(status["incident"]["run_id"]) is (automatic and linked)
    assert len(env.queued) == int(automatic and linked)
    if env.queued:
        run = await database.collection("runs").find_one({"id": env.queued[0]})
        record = await database.collection("mail").find_one({"id": report["id"]})
        assert record and run
        actor = await env.service.sources.reports.authorized(env.org, record["subject"])
        assert run["data"]["trigger"]["actor_id"] == actor.actor_id

    duplicate = json.dumps({"type": "email.received", "data": {"email_id": str(uuid4())}}).encode()
    await env.client.post(
        "/v1/webhooks/resend", content=duplicate, headers=email_signature(duplicate)
    )
    await env.service.sources.drain()
    replay = await env.client.post(env.path + "/reports", json=body, headers=request_headers)
    assert replay.json()["id"] == report["id"]
    assert await database.collection("inventory").count_documents({"kind": "incident"}) == 1
    assert len(env.queued) == int(automatic and linked)
    conflict = await env.client.post(
        env.path + "/reports", json=body | {"details": "Changed"}, headers=request_headers
    )
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "change", ["forged", "sender", "expired", "generation", "controls", "source", "scope", "member"]
)
async def test_simulation_rejects_forgery_stale_state_and_removed_access(
    report_env: Any,
    database: Database,
    change: str,
) -> None:
    env = report_env
    await controls(database, env, automatic=True)
    response = await env.client.post(env.path + "/reports", json=payload(env), headers=headers())
    assert response.status_code == 202, response.text
    record = await database.collection("mail").find_one({"id": response.json()["id"]})
    assert record
    ticket = record["ticket"]
    sender = env.service.config.email_sender
    if change == "forged":
        ticket = ticket[:-2] + ("AA" if ticket[-2:] != "AA" else "BB")
    elif change == "sender":
        sender = "attacker@example.com"
    elif change == "expired":
        await database.collection("mail").update_one(
            {"id": record["id"]}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
        )
    elif change in {"generation", "controls"}:
        async with Records(database).write(env.org) as rows:
            credential = await rows.get(ManagedCredential, env.org, env.credential_id)
            changed = {
                "active_generation_id"
                if change == "generation"
                else "control_version": "version_changed",
                "revision": credential.revision + 1,
            }
            await rows.replace(credential.model_copy(update=changed), credential.revision)
    elif change == "source":
        async with Records(database).write(env.org) as rows:
            connection = await rows.get(Connection, env.org, env.box.id)
            await rows.replace(
                connection.model_copy(
                    update={
                        "status": ConnectionStatus.DISABLED,
                        "revision": connection.revision + 1,
                    }
                ),
                connection.revision,
            )
    elif change == "scope":
        async with Records(database).write(env.org) as rows:
            connection = await rows.get(Connection, env.org, env.box.id)
            await rows.replace(
                connection.model_copy(
                    update={
                        "allowed_resources": ("other@example.com",),
                        "revision": connection.revision + 1,
                    }
                ),
                connection.revision,
            )
    elif change == "member":
        await database.collection("members").update_one(
            {"organisation_id": env.org, "subject": record["subject"]},
            {"$set": {"data.enabled": False}},
        )
    data = {"from": sender, "text": "Kinu test reference: " + record["id"] + "." + ticket}
    assert await env.service.sources.reports.receive(env.box, str(uuid4()), data)
    assert not env.queued
    assert await database.collection("inventory").count_documents({"kind": "incident"}) == 0
    assert await database.collection("runs").count_documents({}) == 0


async def test_manual_incident_and_report_endpoints_are_workspace_scoped(
    report_env: Any,
    database: Database,
) -> None:
    env = report_env
    await controls(database, env, automatic=False)
    body = {"credential_id": env.credential_id, "kind": "expiry", "details": "Reported by operator"}
    response = await env.client.post(env.path + "/reports", json=body, headers=headers())
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "completed"
    assert response.json()["incident"]["source"] == "manual"
    assert response.json()["incident"]["description"] == body["details"]
    assert not response.json()["incident"]["run_id"]
    other = f"/v1/organisations/{env.other}/incidents"
    assert (
        await env.client.get(other + "/reports/" + response.json()["id"], headers=HEADERS)
    ).status_code == 404
    assert (
        await env.client.get(
            other + "/report-options", params={"credential_id": env.credential_id}, headers=HEADERS
        )
    ).status_code == 404
    assert (
        await env.client.post(
            "/v1/organisations/org_unowned/incidents/reports", json=body, headers=headers()
        )
    ).status_code == 403
    assert (
        await env.client.post(
            env.path + "/reports", json=body | {"simulate": True}, headers=headers()
        )
    ).status_code == 422
    assert await database.collection("inventory").count_documents({"kind": "incident"}) == 1


async def test_manual_incident_follows_automatic_controls(
    report_env: Any, database: Database
) -> None:
    env = report_env
    await controls(database, env, automatic=True)
    response = await env.client.post(
        env.path + "/reports",
        json={"credential_id": env.credential_id, "kind": "exposure"},
        headers=headers(),
    )
    assert response.status_code == 201, response.text
    assert response.json()["incident"]["source"] == "manual"
    assert response.json()["incident"]["run_id"]
    assert len(env.queued) == 1


async def test_simulation_limits_mail_and_does_not_leak_ticket_in_status(report_env: Any) -> None:
    env = report_env
    responses = [
        await env.client.post(env.path + "/reports", json=payload(env), headers=headers())
        for _ in range(6)
    ]
    assert [response.status_code for response in responses] == [202] * 5 + [409]
    for response in responses[:5]:
        status = await env.client.get(
            env.path + "/reports/" + response.json()["id"], headers=HEADERS
        )
        assert "ticket" not in status.text and "fingerprint" not in status.text


async def test_simulation_retries_mail_with_the_same_delivery_key(
    report_env: Any,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = report_env
    response = await env.client.post(env.path + "/reports", json=payload(env), headers=headers())
    assert response.status_code == 202
    sent = []

    def send(request: httpx.Request) -> httpx.Response:
        sent.append((request.headers["Idempotency-Key"], json.loads(request.content)))
        return (
            httpx.Response(503)
            if len(sent) == 1
            else httpx.Response(200, json={"id": str(uuid4())})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as http:
        monkeypatch.setattr(env.service, "http", http)
        await env.service.sources.reports.drain()
        await database.collection("mail").update_one(
            {"id": response.json()["id"]},
            {"$set": {"available_at": datetime.now(UTC) - timedelta(seconds=1)}},
        )
        await env.service.sources.reports.drain()
    assert len(sent) == 2 and sent[0] == sent[1]
    assert 'alt="Kinu"' in sent[0][1]["html"]
    assert "/dashboard/credentials/" + env.credential_id in sent[0][1]["html"]
    assert (
        await env.client.get(env.path + "/reports/" + response.json()["id"], headers=HEADERS)
    ).json()["state"] == "sent"


async def test_simulation_header_matches_the_server_record_and_cannot_cross_workspaces(
    report_env: Any,
    database: Database,
) -> None:
    env = report_env
    await controls(database, env, automatic=False)
    response = await env.client.post(env.path + "/reports", json=payload(env), headers=headers())
    assert response.status_code == 202
    record = await database.collection("mail").find_one({"id": response.json()["id"]})
    assert record
    incoming = {
        "from": env.service.config.email_sender,
        "text": "Modified message about another key",
        "headers": {"x-kinu-test-reference": record["id"] + "." + record["ticket"]},
    }
    other = await env.service.sources.forwarding.create(env.other)
    assert await env.service.sources.reports.receive(other, str(uuid4()), incoming)
    assert await database.collection("inventory").count_documents({"kind": "incident"}) == 0
    assert await env.service.sources.reports.receive(env.box, str(uuid4()), incoming)
    status = await env.service.sources.reports.status(env.org, record["id"])
    assert status.incident and status.incident.credential_id == env.credential_id
    assert status.incident.kind == "credential-exposure-detected"


async def test_simulation_cannot_rotate_a_key_changed_while_dispatching(
    report_env: Any,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = report_env
    await controls(database, env, automatic=True)
    response = await env.client.post(env.path + "/reports", json=payload(env), headers=headers())
    assert response.status_code == 202
    record = await database.collection("mail").find_one({"id": response.json()["id"]})
    assert record

    async def changed_scope(organisation_id: str, credential_id: str) -> None:
        async with Records(database).write(env.org) as rows:
            credential = await rows.get(ManagedCredential, env.org, env.credential_id)
            await rows.replace(
                credential.model_copy(
                    update={
                        "active_generation_id": "generation_changed",
                        "revision": credential.revision + 1,
                    }
                ),
                credential.revision,
            )

    monkeypatch.setattr(env.service.dispatcher, "require_scope", changed_scope)
    assert await env.service.sources.reports.receive(
        env.box,
        str(uuid4()),
        {
            "from": env.service.config.email_sender,
            "text": "Kinu test reference: " + record["id"] + "." + record["ticket"],
        },
    )
    assert not env.queued
    assert await database.collection("runs").count_documents({}) == 0
