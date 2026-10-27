import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from broker import CapabilitySigner
from browser.access import BrowserAccessService
from browser.auth import BrowserAuthBroker
from browser.driver import AuthenticationRequiredError, BrowserDriver
from browser.playbook import browser_step_context, resolve_playbook_step
from browser.service import BrowserService
from browser.url import metadata_url
from contracts import (
    BrowserAccessMode,
    BrowserAction,
    BrowserActionKind,
    BrowserActionRecord,
    BrowserActionStatus,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    ComputerUseActivity,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    CredentialGeneration,
    GenerationState,
    ManagedCredential,
    PageCheckpoint,
    PlaybookEffect,
    PlaybookStep,
    RotationRun,
    RunStatus,
    SecureCaptureResult,
    Selector,
    SelectorKind,
    Stage,
    Trigger,
)
from core.errors import ResourceConflictError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from testkit import make_http_provider_api, make_run

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_browser_playbook_resolves_run_bound_values_across_the_full_step() -> None:
    run = make_run(NOW).model_copy(
        update={"id": "run_1234567890abcdef1234567890abcdef", "current_generation_id": "gen_old"}
    )
    credential = ManagedCredential(
        id=run.credential_id,
        organisation_id=run.organisation_id,
        connection_id="connection_browser",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        secret_reference="secret://project-one/key/versions/1",
        provider="Resend",
        kind="api-key",
        display_name="Resend key",
        control_version="policy_one",
        created_at=NOW,
        updated_at=NOW,
    )
    old = CredentialGeneration(
        id="gen_old",
        organisation_id=run.organisation_id,
        credential_id=run.credential_id,
        provider_id="/api-keys/old-key-one",
        provider_display_name="kinu",
        state=GenerationState.ACTIVE,
        attempt_id="attempt_old",
        created_at=NOW,
    )
    step = PlaybookStep(
        id="revoke_old_key",
        stage=Stage.REVOKE,
        tool="browser.revokeCredential",
        operation="click",
        objective="Delete the superseded key",
        parameters={"value": "${old_provider_display_name}"},
        selectors=(Selector(kind=SelectorKind.TEXT, value="${old_provider_display_name}"),),
        checkpoint=PageCheckpoint(
            url_pattern="https://resend.com/${old_provider_path}",
            required_text=("${old_provider_display_name}",),
        ),
        evidence_checks=frozenset({"provider-revoked"}),
        effect=PlaybookEffect.REVOKE_CREDENTIAL,
    )
    resolved = resolve_playbook_step(
        step, browser_step_context(run, credential, old, None), protected=True
    )
    assert resolved.parameters == {"value": "kinu"}
    assert resolved.selectors[0].value == "kinu"
    assert resolved.checkpoint is not None
    assert resolved.checkpoint.url_pattern == "https://resend.com/api-keys/old-key-one"
    assert resolved.checkpoint.required_text == ("kinu",)
    assert resolved.protected is True
    assert (
        browser_step_context(run, credential, old, None)["replacement_provider_id"]
        == "resend-1234567890abcdef12345678"
    )
    assert browser_step_context(run, credential, old, None)["old_provider_display_name"] == "kinu"


class Repository:
    def __init__(self) -> None:
        self.session: BrowserSession | None = None
        self.actions: dict[str, BrowserActionRecord] = {}
        self.captures: list[SecureCaptureResult] = []
        self.activity: list[ComputerUseActivity] = []

    async def create(self, session: BrowserSession) -> BrowserSession:
        self.session = session
        return session

    async def get(self, organisation_id: str, session_id: str) -> BrowserSession:
        assert self.session is not None
        return self.session

    async def update(
        self, organisation_id: str, session_id: str, expected_revision: int, changed: BrowserSession
    ) -> BrowserSession:
        assert self.session is not None and self.session.revision == expected_revision
        self.session = changed
        return changed

    async def save_capture(self, result: SecureCaptureResult) -> SecureCaptureResult:
        self.captures.append(result)
        return result

    async def complete_capture(
        self, current: BrowserSession, changed: BrowserSession, result: SecureCaptureResult
    ) -> BrowserSession:
        assert self.session == current
        self.captures.append(result)
        self.session = changed
        return changed

    async def save_activity(self, activity: ComputerUseActivity) -> ComputerUseActivity:
        self.activity.append(activity)
        return activity

    async def begin_action(
        self,
        current: BrowserSession,
        changed: BrowserSession,
        action: BrowserAction,
        authorised_at: datetime,
    ) -> BrowserSession:
        self.actions[action.id] = BrowserActionRecord(
            id=action.id,
            organisation_id=current.organisation_id,
            session_id=current.id,
            action=action,
            status=BrowserActionStatus.AUTHORIZED,
            authorised_at=authorised_at,
        )
        self.session = changed
        return changed

    async def finish_action(
        self,
        organisation_id: str,
        session_id: str,
        action_id: str,
        status: BrowserActionStatus,
        error: str | None,
        completed_at: datetime,
    ) -> BrowserActionRecord:
        current = self.actions[action_id]
        changed = current.model_copy(
            update={"status": status, "error": error, "completed_at": completed_at}
        )
        self.actions[action_id] = changed
        return changed


@pytest.mark.anyio
async def test_secure_capture_barriers_remain_until_result_is_persisted() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    session = await service.arm_capture("org_one", session.id, session.revision)
    assert session.status is BrowserStatus.CAPTURING
    assert session.model_paused is True
    assert session.recording_paused is True
    result = SecureCaptureResult(
        id="capture_one",
        organisation_id="org_one",
        session_id=session.id,
        field_name="api_key",
        provider_id="provider-key-one",
        secret_reference="secret://project-one/key/versions/4",
        fingerprint="a" * 64,
        masked_value_digest="b" * 64,
        captured_at=NOW,
    )
    session = await service.complete_capture(result, session.revision)
    assert repository.captures == [result]
    assert session.status is BrowserStatus.RUNNING
    assert session.model_paused is False
    assert session.recording_paused is False


@pytest.mark.anyio
async def test_stale_browser_action_is_rejected_before_authorisation() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    action = BrowserAction(
        id="action_one",
        session_id=session.id,
        kind=BrowserActionKind.CLICK,
        selector=Selector(kind=SelectorKind.ROLE, value="button", name="Continue"),
        fencing_token=9,
    )
    with pytest.raises(ResourceConflictError, match="stale"):
        await service.authorize_action("org_one", session.id, session.revision, action)
    assert repository.actions == {}


@pytest.mark.anyio
async def test_browser_input_value_is_not_persisted_in_action_history() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    action = BrowserAction(
        id="action_one",
        session_id=session.id,
        kind=BrowserActionKind.TYPE,
        selector=Selector(kind=SelectorKind.LABEL, value="Account name"),
        value="operator-supplied-value",
        fencing_token=session.fencing_token,
    )
    await service.authorize_action("org_one", session.id, session.revision, action)
    assert repository.actions[action.id].action.value == "<redacted>"


@pytest.mark.anyio
async def test_browser_navigation_query_and_fragment_are_not_persisted() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    action = BrowserAction(
        id="action_one",
        session_id=session.id,
        kind=BrowserActionKind.NAVIGATE,
        url="https://console.vendor.example.com/callback?code=secret#token",
        fencing_token=session.fencing_token,
    )
    await service.authorize_action("org_one", session.id, session.revision, action)
    assert repository.actions[action.id].action.url == "https://console.vendor.example.com/callback"


def test_browser_metadata_url_keeps_only_origin_and_path() -> None:
    assert (
        metadata_url("https://vendor.example/callback?code=secret#fragment")
        == "https://vendor.example/callback"
    )


@pytest.mark.anyio
async def test_takeover_capability_is_identity_bound_and_releases_to_a_safe_pause() -> None:
    repository = Repository()
    sessions = BrowserService(repository, lambda: NOW)
    session = await sessions.create(_session())
    session = await sessions.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await sessions.start("org_one", session.id, session.revision)
    session = await sessions.freeze("org_one", session.id, session.revision)
    run = RotationRun(
        id="run_one",
        organisation_id="org_one",
        credential_id="credential_one",
        trigger=Trigger(
            source="incident",
            event_id="event_one",
            actor_id="actor_one",
            reason="credential exposure",
            urgency="high",
            received_at=NOW,
        ),
        control_version="policy_one",
        status=RunStatus.PAUSED,
        created_at=NOW,
        updated_at=NOW,
    )
    catalog = Catalog(repository, run)
    signer = CapabilitySigner(b"x" * 32)

    async def load_signer() -> CapabilitySigner:
        return signer

    class SecretAccess:
        def __init__(self) -> None:
            self.installed: list[str] = []

        async def install(self, run: RotationRun, session: BrowserSession) -> datetime:
            self.installed.append(f"{run.id}:{session.id}")
            return NOW + timedelta(minutes=5)

    secret_access = SecretAccess()
    access = BrowserAccessService(
        catalog, sessions, load_signer, "https://browser.example.com", lambda: NOW, secret_access
    )
    grant = await access.issue(
        "org_one", session.id, BrowserAccessMode.TAKEOVER, "operator-subject"
    )
    claims = signer.verify(grant.capability, NOW)
    assert grant.session.status is BrowserStatus.TAKEOVER
    assert grant.session.takeover_subject == "operator-subject"
    assert grant.session.recording_paused is True
    assert claims.tool == "browser.takeover"
    assert claims.agent_id.startswith("actor_")
    assert secret_access.installed == ["run_one:session_one"]
    released = await access.release("org_one", session.id, "operator-subject")
    rebound = await sessions.rebind_fence("org_one", session.id, released.revision, 4)
    assert released.status is BrowserStatus.PAUSED
    assert released.recording_paused is True
    assert rebound.fencing_token == 4


def test_browser_domain_allowlist_does_not_accept_lookalikes() -> None:
    driver = BrowserDriver(cast(Any, None), _session().policy)
    driver.validate_url("https://console.vendor.example.com/keys")
    with pytest.raises(AuthenticationRequiredError, match="allowlist"):
        driver.validate_url("https://console.vendor.example.com.attacker.test/keys")
    with pytest.raises(ResourceConflictError, match="credential-free"):
        driver.validate_url("https://user:password@console.vendor.example.com/keys")


@pytest.mark.anyio
async def test_auth_broker_accepts_only_allowlisted_playwright_state() -> None:
    broker = BrowserAuthBroker(cast(Any, AuthSecrets("console.vendor.example.com")))
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="vendor",
        display_name="Vendor console",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        authorization_reference="secret://project/session/versions/1",
        capabilities=frozenset({"browser.authenticate"}),
        allowed_resources=("console.vendor.example.com",),
        playbook_id="playbook_one",
        playbook_version_id="version_one",
        status=ConnectionStatus.READY,
        region="us-central1",
        created_at=NOW,
        updated_at=NOW,
    )
    state = await broker.storage_state(connection, ("*.vendor.example.com",))
    assert state["cookies"][0]["name"] == "session"


@pytest.mark.anyio
async def test_auth_broker_rejects_cross_domain_cookie() -> None:
    broker = BrowserAuthBroker(cast(Any, AuthSecrets("attacker.example")))
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="vendor",
        display_name="Vendor console",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        authorization_reference="secret://project/session/versions/1",
        capabilities=frozenset({"browser.authenticate"}),
        allowed_resources=("console.vendor.example.com",),
        playbook_id="playbook_one",
        playbook_version_id="version_one",
        status=ConnectionStatus.READY,
        region="us-central1",
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(ResourceConflictError, match="outside"):
        await broker.storage_state(connection, ("*.vendor.example.com",))


@pytest.mark.anyio
async def test_auth_broker_rejects_api_provider_connections() -> None:
    broker = BrowserAuthBroker(cast(Any, AuthSecrets("console.vendor.example.com")))
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="sendgrid",
        display_name="SendGrid Admin",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="secret://project/admin/versions/1",
        capabilities=frozenset({"create", "revoke"}),
        allowed_resources=("sendgrid:*",),
        http=make_http_provider_api(),
        status=ConnectionStatus.READY,
        region="us-central1",
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(ResourceConflictError, match="browser connection"):
        await broker.storage_state(connection, ("*.vendor.example.com",))


def _session() -> BrowserSession:
    return BrowserSession(
        id="session_one",
        organisation_id="org_one",
        run_id="run_one",
        playbook_id="playbook_one",
        playbook_version="version_one",
        provider_connection_id="provider_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        status=BrowserStatus.PROVISIONING,
        policy=BrowserPolicy(
            allowed_domains=("*.vendor.example.com",),
            allowed_actions=frozenset(
                {BrowserActionKind.NAVIGATE, BrowserActionKind.CLICK, BrowserActionKind.TYPE}
            ),
        ),
        fencing_token=3,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )


class Catalog:
    def __init__(self, repository: Repository, run: RotationRun) -> None:
        self._repository = repository
        self._run = run

    async def get[T](self, path: str, model: type[T]) -> T:
        if model is BrowserSession:
            assert self._repository.session is not None
            return self._repository.session  # type: ignore[return-value]
        if model is RotationRun:
            return self._run  # type: ignore[return-value]
        raise AssertionError(f"unexpected catalog model for {path}")


class AuthSecret:
    def __init__(self, value: bytes) -> None:
        self.value = bytearray(value)

    def bytes(self) -> bytes:
        return bytes(self.value)

    def clear(self) -> None:
        for index in range(len(self.value)):
            self.value[index] = 0


class AuthSecrets:
    def __init__(self, domain: str) -> None:
        self.domain = domain

    async def access_for(self, connection: Connection, version: str) -> AuthSecret:
        assert connection.organisation_id == "org_one"
        assert version.endswith("/versions/1")
        return AuthSecret(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "session",
                            "value": "opaque-cookie",
                            "domain": self.domain,
                            "path": "/",
                        }
                    ],
                    "origins": [],
                }
            ).encode()
        )


class _LoginPage:
    def __init__(self, url: str) -> None:
        self.url = url


def _login_driver(url: str) -> BrowserDriver:
    policy = BrowserPolicy(
        allowed_domains=("*.vendor.example.com",),
        allowed_actions=frozenset({BrowserActionKind.NAVIGATE}),
        login_url_pattern="https://*.vendor.example.com/login*",
    )
    return BrowserDriver(cast(Any, _LoginPage(url)), policy)


@pytest.mark.anyio
async def test_setup_frames_mask_authentication_and_token_controls() -> None:

    class SetupPage:
        def __init__(self) -> None:
            self.selector = ""
            self.mask: list[object] = []
            self.mask_color = ""

        def locator(self, selector: str) -> object:
            self.selector = selector
            return object()

        async def screenshot(self, **kwargs: object) -> bytes:
            mask = kwargs.get("mask")
            assert isinstance(mask, list)
            self.mask = mask
            self.mask_color = str(kwargs.get("mask_color"))
            return b"masked-setup-frame"

    page = SetupPage()
    policy = BrowserPolicy(
        allowed_domains=("*.vendor.example.com",),
        allowed_actions=frozenset({BrowserActionKind.NAVIGATE}),
    )
    driver = BrowserDriver(cast(Any, page), policy)
    frame = await driver.setup_screenshot()
    assert frame == b"masked-setup-frame"
    assert page.mask
    assert page.mask_color == "#3b3f46"
    assert 'input[type="password"]' in page.selector
    assert 'input[autocomplete="one-time-code"]' in page.selector
    assert 'input[name*="token" i]' in page.selector


@pytest.mark.anyio
async def test_setup_driver_uses_normalised_pointer_and_safe_keyboard_input() -> None:

    class Mouse:
        def __init__(self) -> None:
            self.clicks: list[tuple[float, float]] = []
            self.scrolls: list[tuple[int, int]] = []

        async def click(self, x: float, y: float) -> None:
            self.clicks.append((x, y))

        async def wheel(self, x: int, y: int) -> None:
            self.scrolls.append((x, y))

    class Keyboard:
        def __init__(self) -> None:
            self.keys: list[str] = []
            self.inputs: list[str] = []

        async def press(self, value: str) -> None:
            self.keys.append(value)

        async def insert_text(self, value: str) -> None:
            self.inputs.append(value)

    class SetupPage:
        def __init__(self) -> None:
            self.url = "about:blank"
            self.viewport_size = {"width": 1440, "height": 900}
            self.mouse = Mouse()
            self.keyboard = Keyboard()

        async def goto(self, url: str, wait_until: str = "") -> None:
            self.url = url

    page = SetupPage()
    driver = BrowserDriver(
        cast(Any, page),
        BrowserPolicy(
            allowed_domains=("*.vendor.example.com",), allowed_actions=frozenset(BrowserActionKind)
        ),
    )
    await driver.setup_navigate("https://app.vendor.example.com/login")
    await driver.setup_click(500, 500)
    await driver.setup_scroll(240)
    await driver.setup_key("Tab")
    await driver.setup_type("test input")
    assert page.url == "https://app.vendor.example.com/login"
    assert page.mouse.clicks == [(720, 450)]
    assert page.mouse.scrolls == [(0, 240)]
    assert page.keyboard.keys == ["Tab"]
    assert page.keyboard.inputs == ["test input"]


@pytest.mark.anyio
async def test_driver_detects_the_provider_login_wall() -> None:
    driver = _login_driver("https://app.vendor.example.com/login")
    step = PlaybookStep(
        id="step_one",
        stage=Stage.CREATE,
        tool="browser.click",
        operation="create-key",
        objective="create the replacement key",
        selectors=(Selector(kind=SelectorKind.TEST_ID, value="create"),),
        checkpoint=PageCheckpoint(url_pattern="https://app.vendor.example.com/keys"),
        evidence_checks=frozenset({"created"}),
    )
    with pytest.raises(AuthenticationRequiredError, match="login page"):
        await driver.validate_step(step)


@pytest.mark.anyio
async def test_driver_detects_a_login_redirect_after_navigation() -> None:

    class NavigatingPage:
        def __init__(self) -> None:
            self.url = "https://app.vendor.example.com/keys"

        async def goto(self, url: str, wait_until: str = "") -> None:
            self.url = url

    driver = BrowserDriver(cast(Any, NavigatingPage()), _login_driver("")._policy)
    action = BrowserAction(
        id="action_one",
        session_id="session_one",
        kind=BrowserActionKind.NAVIGATE,
        url="https://app.vendor.example.com/login",
        fencing_token=1,
    )
    with pytest.raises(AuthenticationRequiredError, match="login page"):
        await driver.execute(action)


def test_driver_detects_a_login_redirect_with_query_parameters() -> None:
    driver = BrowserDriver(
        cast(Any, _LoginPage("https://app.vendor.example.com/login?redirect=/keys")),
        BrowserPolicy(
            allowed_domains=("*.vendor.example.com",),
            allowed_actions=frozenset({BrowserActionKind.NAVIGATE}),
            login_url_pattern="https://*.vendor.example.com/login",
        ),
    )
    with pytest.raises(AuthenticationRequiredError, match="login page"):
        driver._check_authentication()


@pytest.mark.anyio
async def test_driver_waits_for_client_rendered_checkpoint_text() -> None:

    class TextLocator:
        first: "TextLocator"

        def __init__(self) -> None:
            self.first = self
            self.waited = False

        async def wait_for(self, *, state: str, timeout: int) -> None:
            assert state == "visible"
            assert timeout == 10000
            self.waited = True

    class Page:
        def __init__(self) -> None:
            self.url = "https://app.vendor.example.com/keys"
            self.locator = TextLocator()

        def get_by_text(self, text: str, exact: bool = False) -> TextLocator:
            assert text == "API Keys"
            assert exact is True
            return self.locator

    page = Page()
    driver = BrowserDriver(cast(Any, page), _login_driver("")._policy)
    await driver._validate_text(("API Keys",), ())
    assert page.locator.waited is True


@pytest.mark.anyio
async def test_driver_detects_a_client_rendered_login_redirect_while_waiting() -> None:

    class TextLocator:
        first: "TextLocator"

        def __init__(self, page: "Page") -> None:
            self.first = self
            self.page = page

        async def wait_for(self, *, state: str, timeout: int) -> None:
            assert state == "visible"
            assert timeout == 10000
            self.page.url = "https://app.vendor.example.com/login?redirect=/keys"
            raise PlaywrightTimeoutError("checkpoint timed out")

    class Page:
        def __init__(self) -> None:
            self.url = "https://app.vendor.example.com/keys"
            self.locator = TextLocator(self)

        def get_by_text(self, text: str, exact: bool = False) -> TextLocator:
            assert text == "API Keys"
            assert exact is True
            return self.locator

    driver = BrowserDriver(cast(Any, Page()), _login_driver("")._policy)
    with pytest.raises(AuthenticationRequiredError, match="login page"):
        await driver._validate_text(("API Keys",), ())


@pytest.mark.anyio
async def test_driver_selection_supports_custom_combobox_controls() -> None:

    class Locator:
        def __init__(self, tag: str, visible: bool = True) -> None:
            self.tag = tag
            self.visible = visible
            self.clicked = False

        async def evaluate(self, expression: str) -> str:
            assert expression == "element => element.tagName.toLowerCase()"
            return self.tag

        async def click(self) -> None:
            self.clicked = True

        async def count(self) -> int:
            return 1

        async def is_visible(self) -> bool:
            return self.visible

    class Page:
        def __init__(self) -> None:
            self.field = Locator("button")
            self.option = Locator("div")

        def get_by_label(self, value: str, exact: bool) -> Locator:
            assert value == "Permission"
            assert exact is True
            return self.field

        def get_by_role(self, role: str, name: str, exact: bool) -> Locator:
            assert role == "option"
            assert name == "Sending access"
            assert exact is True
            return self.option

        def get_by_text(self, value: str, exact: bool) -> Locator:
            raise AssertionError(f"unexpected fallback for {value!r}, exact={exact}")

    driver = BrowserDriver(cast(Any, Page()), _login_driver("")._policy)
    await driver._select_option(
        Selector(kind=SelectorKind.LABEL, value="Permission"), "Sending access"
    )
    page = cast(Any, driver._page)
    assert page.field.clicked is True
    assert page.option.clicked is True


def test_blocked_redirect_is_authentication_required() -> None:
    driver = _login_driver("https://app.vendor.example.com/keys")
    driver._blocked_egress = True
    with pytest.raises(AuthenticationRequiredError, match="off the allowlist"):
        driver._check_blocked_egress()


@pytest.mark.anyio
async def test_blocked_subresource_does_not_require_reauthentication() -> None:
    main_frame = object()
    page = SimpleNamespace(url="https://app.vendor.example.com/keys", main_frame=main_frame)
    driver = BrowserDriver(cast(Any, page), _login_driver("")._policy)
    route = SimpleNamespace(abort=AsyncMock())
    request = SimpleNamespace(
        url="https://analytics.example.net/script.js",
        frame=main_frame,
        is_navigation_request=lambda: False,
    )
    await driver._route(cast(Any, route), cast(Any, request))
    route.abort.assert_awaited_once_with("blockedbyclient")
    assert driver._blocked_egress is False


@pytest.mark.anyio
async def test_blocked_top_level_navigation_requires_reauthentication() -> None:
    main_frame = object()
    page = SimpleNamespace(url="https://app.vendor.example.com/keys", main_frame=main_frame)
    driver = BrowserDriver(cast(Any, page), _login_driver("")._policy)
    route = SimpleNamespace(abort=AsyncMock())
    request = SimpleNamespace(
        url="https://identity.example.net/login",
        frame=main_frame,
        is_navigation_request=lambda: True,
    )
    await driver._route(cast(Any, route), cast(Any, request))
    route.abort.assert_awaited_once_with("blockedbyclient")
    assert driver._blocked_egress is True


@pytest.mark.anyio
async def test_terminated_browser_can_be_reprovisioned() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.terminate("org_one", session.id, session.revision)
    session = await service.reprovision(
        "org_one",
        session.id,
        session.revision,
        "connection_browser",
        "playbook_one",
        "version_one",
        "secret_one",
        "secret://project-one/key",
        session.policy,
        4,
        NOW + timedelta(hours=2),
    )
    assert session.status is BrowserStatus.PROVISIONING
    assert session.provider_connection_id == "connection_browser"
    assert session.worker_instance is None
    assert session.fencing_token == 4
    assert session.terminated_at is None
