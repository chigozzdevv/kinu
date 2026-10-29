import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api.app import create_app
from browser.images import Images
from contracts import (
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    CreateRunCommand,
    Stage,
    Trigger,
)
from core.auth import AuthenticatedIdentity
from core.config import Settings
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from executor.vault import Vault
from pydantic import SecretStr
from tests.browser import HEADERS, make_browser
from tests.storage.browser.fixture import Launcher, executable
from tests.storage.browser.test import definition
from tests.storage.hosting.rotation.fixture import Platform
from tests.storage.services.test import workspace

pytestmark = pytest.mark.anyio


def session() -> BrowserSession:
    now = datetime.now(UTC)
    return BrowserSession(
        id="session_image",
        organisation_id="org_one",
        run_id="run_one",
        playbook_id="book_one",
        playbook_version="version_one",
        provider_connection_id="provider_one",
        secret_store_connection_id="store_one",
        secret_resource="kinu://key",
        status=BrowserStatus.RUNNING,
        model_paused=False,
        recording_paused=False,
        policy=BrowserPolicy(
            allowed_domains=("portal.example",), allowed_actions=frozenset(BrowserActionKind)
        ),
        fencing_token=1,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=10),
    )


async def test_evidence_masks_inputs_and_token_text_and_blocks_capture_pages() -> None:
    executable()
    async with Launcher(Platform()).open(definition()) as tab:
        await tab.driver.setup_navigate("https://portal.example/keys")
        await tab.page.set_content(
            '<input id="password" value="private-password">'
            '<div id="token">re_' + "A" * 36 + "</div>"
            '<button id="submit">Create scoped key</button>'
        )
        boxes = [
            await tab.page.locator(selector).bounding_box() for selector in ("#password", "#token")
        ]
        image = await tab.driver.evidence_screenshot(session())
        pixels = await tab.page.evaluate(
            """async ({image, boxes}) => {
          const img = new Image(); img.src = 'data:image/png;base64,' + image; await img.decode();
          const canvas = document.createElement('canvas');
          canvas.width = img.width; canvas.height = img.height;
          const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0);
          return boxes.map(box => Array.from(ctx.getImageData(
            Math.floor(box.x + box.width / 2), Math.floor(box.y + box.height / 2), 1, 1).data));
        }""",
            {"image": base64.b64encode(image).decode(), "boxes": boxes},
        )
        assert pixels == [[59, 63, 70, 255], [59, 63, 70, 255]]
        assert await tab.page.locator("[data-kinu-evidence-mask]").count() == 0
        blocked = session().model_copy(
            update={
                "status": BrowserStatus.CAPTURING,
                "model_paused": True,
                "recording_paused": True,
            }
        )
        with pytest.raises(ResourceConflictError, match="blocked during private capture"):
            await tab.driver.evidence_screenshot(blocked)


async def test_images_are_encrypted_scoped_verified_and_expire(
    database: Database,
) -> None:
    vault = Vault(database, SecretStr("browser-image-private-test-encryption-key"))
    images = Images(vault)
    await images.initialize()
    content = b"\x89PNG\r\n\x1a\n" + b"private-image-fixture" * 4
    reference, digest = await images.save("org_one", "run_one", "activity_one", content)
    assert reference.endswith("/activity_one")
    assert await images.read_image("org_one", "run_one", "activity_one", digest) == (
        content,
        "image/png",
    )
    row = await images.collection.find_one({})
    assert row and base64.b64encode(content).decode() not in row["value"]
    for org, run in (("org_other", "run_one"), ("org_one", "run_other")):
        with pytest.raises(ResourceNotFoundError):
            await images.read_image(org, run, "activity_one", digest)
    with pytest.raises(ResourceConflictError, match="digest"):
        await images.read_image("org_one", "run_one", "activity_one", "a" * 64)
    with pytest.raises(ResourceConflictError, match="evidence changed"):
        await images.save("org_one", "run_one", "activity_one", content + b"changed")
    await images.collection.update_one(
        {}, {"$set": {"expires_on": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    with pytest.raises(ResourceNotFoundError, match="expired"):
        await images.read_image("org_one", "run_one", "activity_one", digest)


async def test_image_history_endpoint_enforces_workspace_run_and_activity_scope(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await workspace(database, identity)
    now = datetime.now(UTC)
    run = (
        await RunWorkflow(RunStore(database)).create(
            CreateRunCommand(
                id="command_image",
                organisation_id=org,
                credential_id="credential_image",
                control_version="controls_image",
                trigger=Trigger(
                    source="manual",
                    kind="manual",
                    event_id="image_check",
                    actor_id=identity.actor_id,
                    reason="Image access check",
                    urgency="routine",
                    received_at=now,
                ),
            )
        )
    ).run
    vault = Vault(database, SecretStr("browser-image-private-test-encryption-key"))
    images = Images(vault)
    content = b"\x89PNG\r\n\x1a\n" + b"private-image-fixture" * 4
    reference, digest = await images.save(org, run.id, "activity_image", content)
    activity = ComputerUseActivity(
        id="activity_image",
        organisation_id=org,
        run_id=run.id,
        session_id="browser_image",
        step_id="create_key",
        stage=Stage.CREATE,
        turn=1,
        phase=ComputerUseActivityPhase.PROPOSAL,
        status=ComputerUseActivityStatus.PROPOSED,
        action=BrowserActionKind.CLICK,
        image_reference=reference,
        image_digest=digest,
        recorded_at=now,
    )
    await database.collection("browser_activity").insert_one(activity.model_dump(mode="json"))
    monkeypatch.setattr("api.runtime.application_database", lambda config: database)
    app = create_app(browser=make_browser(identity), settings=Settings())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        app.state.services = replace(app.state.services, history=HistoryStore(database, images))
        path = f"/v1/organisations/{org}/runs/{run.id}/computer-use/activity_image/image"
        response = await client.get(path, headers=HEADERS)
        assert response.status_code == 200 and response.content == content
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["content-type"] == "image/png"
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(path.replace(org, "org_other"), headers=HEADERS)
        ).status_code == 403
        assert (
            await client.get(path.replace(run.id, "run_other"), headers=HEADERS)
        ).status_code == 404
        assert (
            await client.get(path.replace("activity_image", "activity_other"), headers=HEADERS)
        ).status_code == 404
