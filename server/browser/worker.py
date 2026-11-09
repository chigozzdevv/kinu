import json
from datetime import UTC, datetime
from typing import Any

from capture import SecureCapture
from connectors.base import SecretValue
from contracts import (
    BrowserAction,
    BrowserActionKind,
    BrowserActionResult,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    Connection,
    CredentialGeneration,
    ManagedCredential,
    PlaybookEffect,
    PlaybookStep,
    RotationRun,
    RunStatus,
    SecureCaptureResult,
    Stage,
)
from core.errors import ActiveRunConflictError, ResourceConflictError
from core.inventory.store import InventoryStore
from core.storage.records import Records as InventoryRecords
from core.storage.runs import RunStore
from executor.versions import Versions
from rotation.guard import validate
from rotation.snapshot import snapshot
from rotation.stages import stable

from browser.catalog import Catalog
from browser.identity import normalize
from browser.images import Images
from browser.launch import BrowserFactory, Launcher
from browser.planner import ControlPlanner
from browser.playbook import browser_step_context, resolve_playbook_step
from browser.proposal import Proposals
from browser.records import Records
from browser.service import BrowserService

TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.COMPENSATED}


class Sink:
    def __init__(self, versions: Versions, run_id: str) -> None:
        self.versions, self.run_id = versions, run_id

    async def add_version_for(
        self,
        connection: Connection,
        secret: str,
        value: SecretValue,
        access_token: SecretValue | None = None,
    ) -> dict[str, object]:
        return {
            "secret_reference": await self.versions.record(
                connection, secret, stable("version", self.run_id), value
            )
        }

    async def disable_for(
        self, connection: Connection, reference: str, access_token: SecretValue | None = None
    ) -> dict[str, Any]:
        return await self.versions.disable_for(connection, reference)


class Worker:
    def __init__(
        self,
        catalog: Catalog,
        versions: Versions,
        planner: ControlPlanner,
        launcher: BrowserFactory | None = None,
    ) -> None:
        self.catalog, self.versions = catalog, versions
        self.database, self.launcher = catalog.database, launcher or Launcher()
        self.images = Images(catalog.vault)
        self.sessions = BrowserService(Records(self.database), lambda: datetime.now(UTC))
        self.proposals = Proposals(self.sessions, self.images, planner)

    async def lock(self, connection: Connection, run: RotationRun) -> None:
        org = connection.organisation_id
        async with self.database.transaction(org) as session:
            row = await self.database.collection("browser_locks").find_one(
                {"organisation_id": org, "connection_id": connection.id}, session=session
            )
            if row and row["run_id"] != run.id:
                previous = await RunStore(self.database).get(org, row["run_id"])
                if previous.status not in TERMINAL:
                    raise ActiveRunConflictError("Another rotation uses this provider browser")
            await self.database.collection("browser_locks").update_one(
                {"organisation_id": org, "connection_id": connection.id},
                {"$set": {"run_id": run.id}},
                upsert=True,
                session=session,
            )

    async def ids(self, connection: Connection) -> tuple[str, ...]:
        version = await self.catalog.definition(connection)
        config = version.definition.verifier
        if config is None:
            return ()
        first = next(
            (
                step
                for step in version.definition.steps
                if step.tool == "browser.navigate" and step.stage is Stage.CREATE
            ),
            None,
        )
        if first is None:
            raise ResourceConflictError("Browser playbook needs a key-list navigation step")
        async with (
            self.catalog.authenticated(connection),
            self.launcher.open(version.definition, await self.catalog.state(connection)) as tab,
        ):
            await tab.driver.setup_navigate(str(first.parameters["url"]))
            await tab.driver.validate_step(first)
            locator = await tab.driver.locator(config.credential_ids, require_unique=False)
            if await locator.count() > 1000:
                raise ResourceConflictError("Provider key list exceeds the inspection limit")
            values = await locator.evaluate_all(
                "(elements, attribute) => elements.map(element => "
                "attribute === 'text' ? element.textContent : "
                "attribute === 'value' ? element.value : element.getAttribute(attribute))",
                config.credential_ids_attribute,
            )
            try:
                identities = tuple(
                    normalize(value, config.credential_id_path, tab.driver.url) for value in values
                )
            except (ValueError, TypeError, AttributeError):
                raise ResourceConflictError("Provider key identifiers are invalid") from None
            if len(set(identities)) != len(identities):
                raise ResourceConflictError("Provider key identifiers are missing or ambiguous")
            return identities

    async def perform(
        self,
        connection: Connection,
        run: RotationRun,
        stage: Stage,
        revoke_id: str | None = None,
        revoke_name: str | None = None,
    ) -> SecureCaptureResult | None:
        org = run.organisation_id
        state = await snapshot(self.database, org, run.credential_id, run.control_version)
        if state.provider != connection or not run.lease:
            raise ResourceConflictError("Browser provider changed")
        fence = {"run_id": run.id, "fencing_token": run.fencing_token}
        await validate(self.database, state, fence)
        await self.lock(connection, run)
        version = await self.catalog.definition(connection)
        steps = tuple(step for step in version.definition.steps if step.stage is stage)
        if run.browser_playbook_version != version.id:
            raise ResourceConflictError("Rotation did not pin this browser playbook")
        if stage is Stage.REVOKE and not any(
            token in json.dumps([step.model_dump(mode="json") for step in steps])
            for token in ("${old_provider_id}", "${old_provider_path}")
        ):
            raise ResourceConflictError("Revocation must bind the selected provider key ID")
        if not steps:
            raise ResourceConflictError("Browser playbook has no steps for this stage")
        supported = {
            "browser.navigate",
            "browser.type",
            "browser.select",
            "browser.click",
            "browser.secure-capture",
            "browser.revokeCredential",
        }
        if any(step.tool not in supported for step in steps):
            raise ResourceConflictError("Browser playbook contains an unsupported action")
        async with InventoryRecords(self.database).read() as rows:
            credential = await rows.get(ManagedCredential, org, run.credential_id)
            old = await rows.get(CredentialGeneration, org, credential.active_generation_id or "")
        context = browser_step_context(run, credential, old, None)
        if revoke_id:
            context.update(old_provider_id=revoke_id, old_provider_path=revoke_id.lstrip("/"))
        if revoke_name:
            context["old_provider_display_name"] = revoke_name
        policy = BrowserPolicy(
            allowed_domains=version.definition.allowed_domains,
            allowed_actions=frozenset(BrowserActionKind),
            login_url_pattern=version.definition.login_url_pattern,
        )
        now = datetime.now(UTC)
        session = await self.sessions.create(
            BrowserSession(
                id=stable(
                    "browser",
                    f"{run.id}:{stage.value}:{revoke_id or ''}:{run.fencing_token}",
                ),
                organisation_id=org,
                run_id=run.id,
                playbook_id=version.playbook_id,
                playbook_version=version.id,
                provider_connection_id=connection.id,
                secret_store_connection_id=state.store.id,
                secret_resource=credential.secret_resource,
                status=BrowserStatus.PROVISIONING,
                policy=policy,
                fencing_token=run.fencing_token,
                created_at=now,
                updated_at=now,
                expires_at=run.lease.expires_at,
            )
        )
        session = await self.sessions.attach(
            org, session.id, session.revision, "executor", "private"
        )
        session = await self.sessions.start(org, session.id, session.revision)
        try:
            async with (
                self.catalog.authenticated(connection),
                self.launcher.open(version.definition, await self.catalog.state(connection)) as tab,
            ):
                for index, raw in enumerate(steps):
                    step = resolve_playbook_step(
                        raw, context, protected=raw.effect is not PlaybookEffect.NONE
                    )
                    await validate(self.database, state, fence)
                    if step.tool != "browser.navigate":
                        await tab.driver.validate_step(step)
                    kind = {
                        "browser.navigate": BrowserActionKind.NAVIGATE,
                        "browser.type": BrowserActionKind.TYPE,
                        "browser.select": BrowserActionKind.SELECT,
                    }.get(step.tool, BrowserActionKind.CLICK)
                    result_image: tuple[str, str] | None = None
                    if step.tool != "browser.navigate":
                        await self.proposals.record(session, step, version, tab.driver)
                        await validate(self.database, state, fence)
                    if step.effect is PlaybookEffect.CREATE_CREDENTIAL:
                        session = await self.sessions.arm_capture(org, session.id, session.revision)
                    value, url = step.parameters.get("value"), step.parameters.get("url")
                    if (value is not None and not isinstance(value, str)) or (
                        url is not None and not isinstance(url, str)
                    ):
                        raise ResourceConflictError("Browser input must be text")
                    action = BrowserAction(
                        id=stable("action", session.id + ":" + str(index)),
                        session_id=session.id,
                        kind=kind,
                        selector=step.selectors[0] if step.selectors else None,
                        value=value,
                        url=url,
                        protected=step.effect is not PlaybookEffect.NONE,
                        fencing_token=run.fencing_token,
                    )
                    session = await self.sessions.authorize_action(
                        org, session.id, session.revision, action
                    )
                    capture = None
                    try:
                        await tab.driver.execute(action)
                        if step.effect is PlaybookEffect.CREATE_CREDENTIAL:
                            assert step.secure_field and step.checkpoint
                            field = await tab.driver.locator(
                                step.secure_field.selector, require_unique=False
                            )
                            await field.wait_for(
                                state="visible", timeout=min(step.timeout_seconds, 30) * 1000
                            )

                            async def save_image(
                                content: bytes,
                                activity_id: str = stable(
                                    "activity", session.id + ":" + step.id + ":executed"
                                ),
                            ) -> None:
                                nonlocal result_image
                                result_image = await self.images.save(
                                    org,
                                    run.id,
                                    activity_id,
                                    content,
                                )

                            capture = await SecureCapture(
                                tab.page,
                                tab.driver,
                                Sink(self.versions, run.id),
                                InventoryStore(self.database),
                                lambda: datetime.now(UTC),
                                evidence=save_image,
                            ).transfer(
                                stable("capture", run.id),
                                org,
                                session.id,
                                step.secure_field,
                                step.result_checkpoint or step.checkpoint,
                                state.store.id,
                                credential.secret_resource,
                            )
                        elif step.checkpoint:
                            await tab.driver.validate_checkpoint(
                                step.result_checkpoint or step.checkpoint
                            )
                    except Exception:
                        await self.sessions.finish_action(
                            org, session.id, action.id, False, "Browser action did not complete"
                        )
                        if step.tool != "browser.navigate":
                            await self._receipt(
                                session, step, kind, ComputerUseActivityStatus.FAILED
                            )
                        raise
                    await self.sessions.finish_action(org, session.id, action.id, True)
                    if capture:
                        session = await self.sessions.complete_capture(capture, session.revision)
                    if step.tool != "browser.navigate":
                        result = None
                        if step.effect is not PlaybookEffect.NONE:
                            result = BrowserActionResult(
                                provider_id=capture.provider_id
                                if capture
                                else str(context["old_provider_id"]),
                                secret_reference=capture.secret_reference if capture else None,
                            )
                        await self._receipt(
                            session,
                            step,
                            kind,
                            ComputerUseActivityStatus.SUCCEEDED,
                            result,
                            image=result_image,
                        )
                    if capture:
                        return capture
                return None
        finally:
            if session.status is not BrowserStatus.TERMINATED:
                await self.sessions.terminate(org, session.id, session.revision)

    async def _receipt(
        self,
        session: BrowserSession,
        step: PlaybookStep,
        kind: BrowserActionKind,
        status: ComputerUseActivityStatus,
        result: BrowserActionResult | None = None,
        image: tuple[str, str] | None = None,
    ) -> None:
        await self.sessions.record_activity(
            session,
            ComputerUseActivity(
                id=stable("activity", session.id + ":" + step.id + ":executed"),
                organisation_id=session.organisation_id,
                session_id=session.id,
                run_id=session.run_id,
                step_id=step.id,
                stage=step.stage,
                turn=session.step_count,
                phase=ComputerUseActivityPhase.EXECUTION,
                status=status,
                action=kind,
                target=step.id,
                safety_decision="approved-control",
                image_reference=image[0] if image else None,
                image_digest=image[1] if image else None,
                result=result,
                recorded_at=datetime.now(UTC),
            ),
        )
