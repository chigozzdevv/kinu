from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from capture import CaptureError, SecureCapture
from connectors.base import SecretValue
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    PageCheckpoint,
    SecureField,
    Selector,
    SelectorKind,
)

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Locator:
    def __init__(self, value: str = "one-time-key", href: str | None = None) -> None:
        self.masked = False
        self.value = value
        self.href = href

    async def evaluate(self, script: str, values: Any = None) -> str:
        if "tagName" in script:
            return "input"
        assert values == {"secret": "one-time-key", "mask": "••••••••"}
        self.masked = True
        return '<input data-kinu-capture="masked" aria-label="Credential stored securely">'

    async def input_value(self) -> str:
        return self.value

    async def text_content(self) -> str | None:
        return None

    async def get_attribute(self, name: str) -> str | None:
        assert name == "href"
        return self.href


class Text:
    async def count(self) -> int:
        return 1


class Page:
    url = "https://console.vendor.example.com/keys/created"

    def __init__(self, locator: Locator, exposed: bool = False) -> None:
        self.locator = locator
        self.exposed = exposed
        self.clipboard_cleared = False

    def get_by_text(self, text: str, exact: bool = True) -> Text:
        assert text == "Key created" and exact
        return Text()

    async def evaluate(self, script: str, value: Any = None) -> Any:
        if "navigator.clipboard" in script:
            self.clipboard_cleared = True
            return None
        assert value == "one-time-key"
        assert self.locator.masked is True
        return self.exposed


class Driver:
    def __init__(self, locator: Locator) -> None:
        self.value = locator
        self.provider = Locator("provider-key-one", "/api-keys/provider-key-one")
        self.provider_name = Locator("resend-run-one")

    async def locator(self, selector: Selector) -> Locator:
        if selector.value == "new-key-id":
            return self.provider
        if selector.value == "new-key-name":
            return self.provider_name
        return self.value


class Secrets:
    def __init__(self) -> None:
        self.seen: bytes | None = None
        self.disabled: list[str] = []

    async def add_version_for(
        self, connection: Connection, secret: str, value: Any, access_token: SecretValue
    ) -> dict[str, str]:
        assert connection.id == "sink_one"
        assert secret == "secret://project-one/key"
        self.seen = value.bytes()
        assert access_token.bytes() == b"ephemeral-access"
        return {"secret_reference": "secret://project-one/key/versions/7"}

    async def disable_for(
        self, connection: Connection, version: str, access_token: SecretValue
    ) -> dict[str, str]:
        assert connection.id == "sink_one"
        assert access_token.bytes() == b"ephemeral-access"
        self.disabled.append(version)
        return {"name": version, "state": "DISABLED"}


class Connections:
    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return Connection(
            id=resource_id,
            organisation_id=organisation_id,
            platform="secret-store",
            display_name="Production secret store",
            roles=frozenset({ConnectionRole.SECRET_STORE}),
            interface=ConnectionInterface.API,
            authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
            authorization_reference="workload-identity://capture@project-one.identity.example.com",
            capabilities=frozenset({"secretStore.createVersion"}),
            allowed_resources=("secret://project-one/key",),
            status=ConnectionStatus.READY,
            region="us-east1",
            created_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.anyio
async def test_capture_stores_masks_checks_and_only_returns_reference() -> None:
    locator = Locator()
    page = Page(locator)
    secrets = Secrets()
    capture = SecureCapture(
        cast(Any, page), cast(Any, Driver(locator)), cast(Any, secrets), Connections(), lambda: NOW
    )
    with SecretValue(b"ephemeral-access") as access:
        result = await capture.transfer(
            "capture_one",
            "org_one",
            "session_one",
            _field(),
            _checkpoint(),
            "sink_one",
            "secret://project-one/key",
            access,
        )
    assert secrets.seen == b"one-time-key"
    assert locator.masked is True
    assert page.clipboard_cleared is True
    assert result.secret_reference.endswith("/versions/7")
    assert result.provider_id == "provider-key-one"
    assert "one-time-key" not in result.model_dump_json()


@pytest.mark.anyio
async def test_capture_records_exact_provider_link_and_display_name() -> None:
    locator = Locator()
    capture = SecureCapture(
        cast(Any, Page(locator)),
        cast(Any, Driver(locator)),
        cast(Any, Secrets()),
        Connections(),
        lambda: NOW,
    )
    field = _field().model_copy(
        update={
            "provider_id_attribute": "href",
            "provider_id_path": "/api-keys/",
            "provider_display_name_selector": Selector(
                kind=SelectorKind.TEST_ID, value="new-key-name"
            ),
        }
    )
    with SecretValue(b"ephemeral-access") as access:
        result = await capture.transfer(
            "capture_one",
            "org_one",
            "session_one",
            field,
            _checkpoint(),
            "sink_one",
            "secret://project-one/key",
            access,
        )
    assert result.provider_id == "provider-key-one"
    assert result.provider_display_name == "resend-run-one"


@pytest.mark.anyio
async def test_capture_fails_closed_if_secret_remains_elsewhere_in_dom() -> None:
    locator = Locator()
    secrets = Secrets()
    capture = SecureCapture(
        cast(Any, Page(locator, exposed=True)),
        cast(Any, Driver(locator)),
        cast(Any, secrets),
        Connections(),
        lambda: NOW,
    )
    with SecretValue(b"ephemeral-access") as access, pytest.raises(CaptureError) as raised:
        await capture.transfer(
            "capture_one",
            "org_one",
            "session_one",
            _field(),
            _checkpoint(),
            "sink_one",
            "secret://project-one/key",
            access,
        )
    assert raised.value.secret_reference is not None
    assert raised.value.cleanup_required is False
    assert secrets.disabled == ["secret://project-one/key/versions/7"]
    assert "remains visible" in str(raised.value)


def _field() -> SecureField:
    return SecureField(
        name="api_key",
        selector=Selector(kind=SelectorKind.TEST_ID, value="new-api-key"),
        provider_id_selector=Selector(kind=SelectorKind.TEST_ID, value="new-key-id"),
    )


def _checkpoint() -> PageCheckpoint:
    return PageCheckpoint(
        url_pattern="https://console.vendor.example.com/keys/*", required_text=("Key created",)
    )


@pytest.mark.anyio
@pytest.mark.parametrize("exposed", [False, True])
async def test_result_image_is_taken_only_after_storage_and_verified_masking(
    monkeypatch: pytest.MonkeyPatch, exposed: bool
) -> None:
    locator, secrets = Locator(), Secrets()
    page, frames = Page(locator, exposed), []

    async def snapshot(value: Any, raw: str) -> bytes:
        assert value is page and raw == "one-time-key"
        assert secrets.seen == b"one-time-key" and locator.masked and page.clipboard_cleared
        return b"private-redacted-frame"

    image = AsyncMock(side_effect=snapshot)
    monkeypatch.setattr("capture.service.snapshot", image)

    async def evidence(content: bytes) -> None:
        frames.append(content)

    capture = SecureCapture(
        cast(Any, page),
        cast(Any, Driver(locator)),
        cast(Any, secrets),
        Connections(),
        lambda: NOW,
        evidence=evidence,
    )
    with SecretValue(b"ephemeral-access") as access:
        if exposed:
            with pytest.raises(CaptureError, match="remains visible"):
                await capture.transfer(
                    "capture_one",
                    "org_one",
                    "session_one",
                    _field(),
                    _checkpoint(),
                    "sink_one",
                    "secret://project-one/key",
                    access,
                )
        else:
            await capture.transfer(
                "capture_one",
                "org_one",
                "session_one",
                _field(),
                _checkpoint(),
                "sink_one",
                "secret://project-one/key",
                access,
            )
    assert image.await_count == (0 if exposed else 1)
    assert frames == ([] if exposed else [b"private-redacted-frame"])
