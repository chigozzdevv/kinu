import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from browser.catalog import Catalog
from browser.driver import AuthenticationRequiredError
from browser.probe import Probe
from browser.setupserver import Setups
from browser.worker import Worker
from connectors.http import HttpProviderConnector
from contracts import (
    BrowserSession,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionStatus,
    ControlPreferences,
    CreateRunCommand,
    PageCondition,
    PlaybookDraft,
    RunStatus,
    Selector,
    SelectorKind,
    Trigger,
)
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError
from core.inventory.controls import compile_controls
from core.playbook import PlaybookService
from core.playbook.store import PlaybookStore
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.records import Records
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from executor.authorization import Authorization
from executor.provider import Providers
from hosting.proof import Proof
from policy import digest
from rotation.hosting import Effects
from rotation.rotate import Rotation
from rotation.snapshot import loader
from tests.storage.browser.fixture import Launcher, Planner, executable, response
from tests.storage.hosting.rotation.fixture import NEW, OLD, Platform
from tests.storage.hosting.rotation.test import setup

pytestmark = pytest.mark.anyio


async def test_browser_checks_form_values_before_actions_and_page_after_actions() -> None:
    executable()
    async with Launcher(Platform()).open(definition()) as tab:
        await tab.driver.setup_navigate("https://portal.example/keys")
        await tab.page.set_content(
            '<h1>API keys</h1><input id="scope" value="sending">'
            '<button id="create" onclick="this.remove()">Create</button>'
        )
        step = (
            definition()
            .steps[1]
            .model_copy(
                update={
                    "conditions": (
                        PageCondition(
                            selector=Selector(kind=SelectorKind.CSS, value="#scope"),
                            attribute="value",
                            expected="sending",
                        ),
                    )
                }
            )
        )
        await tab.driver.validate_step(step)
        await tab.page.locator("#scope").fill("full")
        with pytest.raises(ResourceConflictError, match="approved value"):
            await tab.driver.validate_step(step)
        await tab.page.locator("#scope").fill("sending")
        await tab.driver.validate_step(step)
        await tab.page.locator("#create").click()
        assert step.checkpoint
        await tab.driver.validate_checkpoint(step.checkpoint)
        with pytest.raises(ResourceConflictError, match="did not become visible"):
            await tab.driver.validate_step(step)
        await tab.page.set_content('<a href="/keys">API keys</a>')
        await tab.driver.setup_enter("https://portal.example/keys")
        await tab.driver.validate_step(definition().steps[0])
        await tab.driver.setup_navigate("https://portal.example/login")
        with pytest.raises(AuthenticationRequiredError):
            await tab.driver.setup_enter("https://portal.example/keys")


async def test_browser_waits_for_delayed_controls_and_rejects_ambiguous_matches() -> None:
    executable()
    selector = Selector(kind=SelectorKind.ROLE, value="textbox", name="Name")
    async with Launcher(Platform()).open(definition()) as tab:
        await tab.driver.setup_navigate("https://portal.example/keys")
        await tab.page.set_content("<h1>API keys</h1>")
        await tab.page.evaluate(
            "setTimeout(() => document.body.insertAdjacentHTML("
            "'beforeend', '<input aria-label=Name>'), 100)"
        )
        field = await tab.driver.locator(selector)
        await field.fill("replacement")
        assert await field.input_value() == "replacement"
        await tab.page.evaluate(
            "document.body.insertAdjacentHTML('beforeend', '<input aria-label=Name>')"
        )
        with pytest.raises(ResourceConflictError, match="instead of one"):
            await tab.driver.locator(selector)


async def test_browser_waits_for_custom_select_options_to_render() -> None:
    executable()
    async with Launcher(Platform()).open(definition()) as tab:
        await tab.driver.setup_navigate("https://portal.example/keys")
        await tab.page.set_content('<button id="permission">Full access</button>')
        await tab.page.evaluate(
            "document.querySelector('#permission').onclick = () => setTimeout(() => {"
            "const option = document.createElement('button');"
            "option.setAttribute('role', 'option'); option.textContent = 'Sending access';"
            "option.onclick = () => document.querySelector('#permission').textContent = "
            "option.textContent; document.body.appendChild(option);}, 100)"
        )
        await tab.driver._select_option(
            Selector(kind=SelectorKind.CSS, value="#permission"), "Sending access"
        )
        assert await tab.page.locator("#permission").inner_text() == "Sending access"


def definition() -> PlaybookDraft:
    checkpoint = {"url_pattern": "https://portal.example/keys*", "required_text": ["API keys"]}
    base = {"parameters": {}, "evidence_checks": ["page_verified"], "checkpoint": checkpoint}
    return PlaybookDraft.model_validate(
        {
            "name": "Issuer browser",
            "platform": "issuer",
            "allowed_domains": ["portal.example"],
            "login_url_pattern": "https://portal.example/login*",
            "verifier": {
                "base_url": "https://issuer.example",
                "auth": {"scheme": "bearer"},
                "supports_overlap": True,
                "credential_ids": {"kind": "css", "value": ".key-id"},
                "operation": {
                    "method": "GET",
                    "path": "/scopes",
                    "provider_id_field": "id",
                    "success_statuses": [200],
                    "metadata_fields": {"scopes": "scopes"},
                },
            },
            "steps": [
                base
                | {
                    "id": "list_keys",
                    "stage": "create",
                    "tool": "browser.navigate",
                    "operation": "navigate",
                    "objective": "Open API keys",
                    "parameters": {"url": "https://portal.example/keys"},
                },
                base
                | {
                    "id": "create_key",
                    "stage": "create",
                    "effect": "create-credential",
                    "tool": "browser.secure-capture",
                    "operation": "create",
                    "objective": "Create a replacement key",
                    "selectors": [{"kind": "css", "value": "#create"}],
                    "secure_field": {
                        "name": "key",
                        "selector": {"kind": "css", "value": "#key"},
                        "provider_id_selector": {"kind": "css", "value": "#identity"},
                    },
                },
                base
                | {
                    "id": "open_key",
                    "stage": "revoke",
                    "tool": "browser.navigate",
                    "operation": "navigate",
                    "objective": "Open selected key",
                    "parameters": {"url": "https://portal.example/keys/${old_provider_id}"},
                },
                base
                | {
                    "id": "retire_key",
                    "stage": "revoke",
                    "effect": "revoke-credential",
                    "tool": "browser.revokeCredential",
                    "operation": "revoke",
                    "objective": "Retire the selected key",
                    "selectors": [{"kind": "css", "value": "#retire"}],
                },
            ],
        }
    )


@pytest.mark.parametrize(
    "failure,linked",
    [(None, False), (None, True), ("proof", False), ("journal", False), ("agent", False)],
)
async def test_browser_rotation_captures_privately_and_retires_only_the_verified_key(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
    linked: bool,
) -> None:
    executable()
    platform = Platform(failure)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response(platform, request))
    ) as http:
        hosts, versions, _, credential = await setup(database, identity, http)
        org, inventory = credential.organisation_id, hosts.inventory
        books = PlaybookService(PlaybookStore(database), lambda: datetime.now(UTC), inventory)
        draft = definition().model_dump(mode="json")
        if linked:
            draft["verifier"].update(credential_ids_attribute="href", credential_id_path="/keys/")
            draft["steps"][1]["secure_field"].update(
                provider_id_attribute="href", provider_id_path="/keys/"
            )
        _, version = await books.create_version(
            org, "book_one", "version_one", PlaybookDraft.model_validate(draft), identity.actor_id
        )
        await books.publish(org, "book_one", version.id, identity.actor_id)
        original = await inventory.get_connection(org, "provider_one")
        connection = original.model_copy(
            update={
                "interface": ConnectionInterface.BROWSER,
                "http": None,
                "authorization": ConnectionAuthorization.BROWSER_SESSION,
                "authorization_reference": None,
                "status": ConnectionStatus.SETUP_REQUIRED,
                "playbook_id": "book_one",
                "playbook_version_id": version.id,
                "allowed_resources": ("portal.example",),
                "capabilities": HttpProviderConnector.tools
                | {"browser.execute", "browser.authenticate"},
                "revision": original.revision + 1,
            }
        )
        await inventory.replace_connection(connection, original.revision)
        launcher, planner = Launcher(platform), Planner()
        catalog = Catalog(database, hosts.vault)
        setups = Setups(catalog, launcher)
        session, token = await setups.begin(org, connection.id, identity.subject, ())
        with pytest.raises(ResourceConflictError):
            await setups.stream(org, session.id, "another_subject", token, {"type": "frame"})
        with pytest.raises(ResourceConflictError):
            await setups.stream(org, session.id, identity.subject, "invalid", {"type": "frame"})
        await setups.stream(
            org,
            session.id,
            identity.subject,
            token,
            {
                "type": "action",
                "action": {"kind": "navigate", "url": "https://portal.example/keys"},
            },
        )
        assert (await setups.stream(org, session.id, identity.subject, token, {"type": "status"}))[
            "authenticated"
        ]
        if failure is None:
            live = setups.live[session.id]
            await live.tab.page.locator("#login").focus()
            public = await setups.stream(
                org, session.id, identity.subject, token, {"type": "secure-key"}
            )
            key = serialization.load_pem_public_key(public["public_key"].encode())
            assert isinstance(key, rsa.RSAPublicKey)
            ciphertext = key.encrypt(
                b"private-login-value",
                padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
                ),
            )
            typed = await setups.stream(
                org,
                session.id,
                identity.subject,
                token,
                {"type": "secure-input", "ciphertext": base64.b64encode(ciphertext).decode()},
            )
            assert typed == {"type": "secure-input", "succeeded": True}
            assert await live.tab.page.locator("#login").input_value() == "private-login-value"
        _, connection = await setups.complete(
            org, session.id, identity.subject, token, session.revision
        )
        assert not setups.live
        grants = (
            await hosts.vault.database.collection("secrets")
            .find({"organisation_id": org})
            .to_list()
        )
        assert "private-browser-cookie" not in str(grants)
        bindings, services = await inventory.bindings(org), await inventory.services(org)
        controls, probes = compile_controls(
            credential,
            bindings,
            services,
            await inventory.connections(org),
            ControlPreferences(
                automatic_triggers=frozenset({"expiry"}),
                rotate_before_expiry_seconds=86400,
                maximum_observation_seconds=60,
            ),
            identity.actor_id,
            datetime.now(UTC),
        )
        current = await inventory.get_control_version(
            org, credential.id, credential.control_version
        )
        changed = current.model_copy(
            update={
                "id": "controls_browser",
                "number": current.number + 1,
                "definition": controls,
                "digest": digest(controls),
            }
        )
        credential = credential.model_copy(
            update={"control_version": changed.id, "revision": credential.revision + 1}
        )
        await inventory.replace_controls(credential, credential.revision - 1, changed)
        async with Records(database).write(org) as rows:
            for probe in probes:
                await rows.add(probe, org)
        provider = Providers(
            Authorization(hosts.vault, versions),
            Worker(catalog, versions, planner, launcher),
            versions,
            Probe(http),
        )
        planner.stop = failure == "agent"
        activity = provider.browser.sessions.record_activity

        async def record(
            session: BrowserSession, value: ComputerUseActivity
        ) -> ComputerUseActivity:
            if failure == "journal" and value.phase is ComputerUseActivityPhase.EXECUTION:
                raise RuntimeError("audit-store-offline")
            return await activity(session, value)

        monkeypatch.setattr(provider.browser.sessions, "record_activity", record)
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_browser",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="event_browser",
                        actor_id=identity.actor_id,
                        reason="Browser integration check",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run

        async def sleep(seconds: float) -> None:
            assert seconds == 20

        monkeypatch.setattr("rotation.hosting.sleep", sleep)
        effects = Effects(hosts, versions, provider, Proof(http))
        result = await Rotation(database, effects, loader(database), "global").execute(
            org, run.id, "worker_browser"
        )
        assert result.status is (
            RunStatus.CLEANUP
            if failure == "journal"
            else RunStatus.FAILED
            if failure
            else RunStatus.COMPLETED
        ), result.model_dump_json()
        if failure == "journal":
            assert not platform.revoked and all(item["active"] for item in platform.keys.values())
            assert await versions.collection.count_documents({"organisation_id": org}) == 2
            assert not platform.writes
        elif failure == "agent":
            assert not platform.revoked and "new" not in platform.keys
        else:
            assert platform.revoked == (["new"] if failure else ["old"])
            assert all(
                item["active"] is (key == ("old" if failure else "new"))
                for key, item in platform.keys.items()
            )
        records = {}
        for name in (
            "browser_sessions",
            "browser_actions",
            "browser_captures",
            "browser_activity",
            "executions",
        ):
            records[name] = await database.collection(name).find({"organisation_id": org}).to_list()
        serialized = json.dumps(records, default=str)
        assert all(secret not in serialized for secret in (OLD, NEW, "private-browser-cookie"))
        assert len(records["browser_captures"]) == (0 if failure == "agent" else 1)
        assert len(planner.calls) == (1 if failure in {"agent", "journal"} else 2)
        assert all(row["data"]["status"] == "terminated" for row in records["browser_sessions"])
        history = await HistoryStore(database).get(org, run.id)
        assert len(history.computer_use) == len(records["browser_activity"])
        assert all(secret not in history.model_dump_json() for secret in (OLD, NEW))
        if failure is None:
            with pytest.raises(ResourceConflictError, match="sign-in expired"):
                async with catalog.authenticated(connection):
                    raise AuthenticationRequiredError("Provider redirected to login")
            assert (
                await inventory.get_connection(org, connection.id)
            ).status is ConnectionStatus.REAUTHENTICATION
