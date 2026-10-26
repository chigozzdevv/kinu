import fnmatch
import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from browser.driver import BrowserDriver
from connectors.base import SecretValue
from connectors.secrets import SecretStore
from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionRole,
    PageCheckpoint,
    SecureCaptureResult,
    SecureField,
)
from playwright.async_api import Locator, Page

MASK = "••••••••"


class CaptureError(Exception):
    def __init__(
        self,
        message: str,
        secret_reference: str | None = None,
        cleanup_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.secret_reference = secret_reference
        self.cleanup_required = cleanup_required


class CaptureConnections(Protocol):
    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...


class SecureCapture:
    def __init__(
        self,
        page: Page,
        driver: BrowserDriver,
        secrets: SecretStore,
        connections: CaptureConnections,
        clock: Callable[[], datetime],
    ) -> None:
        self._page = page
        self._driver = driver
        self._secrets = secrets
        self._connections = connections
        self._clock = clock

    async def transfer(
        self,
        capture_id: str,
        organisation_id: str,
        session_id: str,
        field: SecureField,
        checkpoint: PageCheckpoint,
        sink_connection_id: str,
        secret_resource: str,
        access_token: SecretValue | None = None,
    ) -> SecureCaptureResult:
        await self._checkpoint(checkpoint)
        locator = await self._driver.locator(field.selector)
        provider = await self._driver.locator(field.provider_id_selector)
        raw = await _read(locator)
        provider_id = await _read(provider, field.provider_id_attribute)
        provider_display_name = await _provider_display_name(self._driver, field)
        if not raw or len(raw) > 16384:
            raise CaptureError("declared secure field is empty or exceeds the capture limit")
        if not provider_id or len(provider_id) > 256:
            raise CaptureError("declared provider identifier is empty or invalid")
        return await self._store_and_mask(
            capture_id,
            organisation_id,
            session_id,
            field,
            sink_connection_id,
            secret_resource,
            raw,
            provider_id,
            provider_display_name,
            locator,
            access_token,
        )

    async def transfer_supplied(
        self,
        capture_id: str,
        organisation_id: str,
        session_id: str,
        field: SecureField,
        checkpoint: PageCheckpoint,
        supplied: bytearray,
        sink_connection_id: str,
        secret_resource: str,
        access_token: SecretValue | None = None,
    ) -> SecureCaptureResult:
        await self._checkpoint(checkpoint)
        locator = await self._driver.locator(field.selector)
        provider = await self._driver.locator(field.provider_id_selector)
        try:
            raw = supplied.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CaptureError("secure input is not valid UTF-8") from error
        if not raw or len(raw) > 16384:
            raise CaptureError("secure input is empty or exceeds the capture limit")
        provider_id = await _read(provider, field.provider_id_attribute)
        provider_display_name = await _provider_display_name(self._driver, field)
        if not provider_id or len(provider_id) > 256:
            raise CaptureError("declared provider identifier is empty or invalid")
        return await self._store_and_mask(
            capture_id,
            organisation_id,
            session_id,
            field,
            sink_connection_id,
            secret_resource,
            raw,
            provider_id,
            provider_display_name,
            locator,
            access_token,
        )

    async def _store_and_mask(
        self,
        capture_id: str,
        organisation_id: str,
        session_id: str,
        field: SecureField,
        sink_connection_id: str,
        secret_resource: str,
        raw: str,
        provider_id: str,
        provider_display_name: str | None,
        locator: Locator,
        access_token: SecretValue | None,
    ) -> SecureCaptureResult:
        secret_bytes = bytearray(raw.encode())
        secret_reference: str | None = None
        connection: Connection | None = None
        try:
            value = SecretValue(secret_bytes)
            try:
                connection = await self._connections.get_connection(
                    organisation_id, sink_connection_id
                )
                if (
                    ConnectionRole.SECRET_STORE not in connection.roles
                    or connection.interface is not ConnectionInterface.API
                    or not _resource_allowed(secret_resource, connection.allowed_resources)
                ):
                    raise CaptureError(
                        "secure capture sink is not an assigned secret store connection"
                    )
                if access_token is None:
                    raise CaptureError("secure capture has no ephemeral secret-store authorization")
                stored = await self._secrets.add_version_for(
                    connection, secret_resource, value, access_token
                )
            finally:
                value.clear()
            secret_reference = _string(stored.get("secret_reference"), "secret reference")
            fingerprint = hashlib.sha256(secret_bytes).hexdigest()
            masked_markup = await locator.evaluate(_MASK_SCRIPT, {"secret": raw, "mask": MASK})
            if not isinstance(masked_markup, str):
                raise CaptureError("secure field masking returned no markup", secret_reference)
            exposed = await self._page.evaluate(_EXPOSURE_SCRIPT, raw)
            if exposed is not False:
                raise CaptureError(
                    "secret remains visible in the document after masking", secret_reference
                )
            await self._clear_clipboard()
            return SecureCaptureResult(
                id=capture_id,
                organisation_id=organisation_id,
                session_id=session_id,
                field_name=field.name,
                provider_id=provider_id,
                provider_display_name=provider_display_name,
                secret_reference=secret_reference,
                fingerprint=fingerprint,
                masked_value_digest=hashlib.sha256(masked_markup.encode()).hexdigest(),
                captured_at=self._clock(),
            )
        except CaptureError as error:
            await self._reconcile_failed_version(connection, secret_reference, access_token, error)
            raise
        except Exception as error:
            failure = CaptureError("secure capture transfer failed", secret_reference)
            await self._reconcile_failed_version(
                connection, secret_reference, access_token, failure
            )
            raise failure from error
        finally:
            for index in range(len(secret_bytes)):
                secret_bytes[index] = 0
            raw = ""

    async def _reconcile_failed_version(
        self,
        connection: Connection | None,
        secret_reference: str | None,
        access_token: SecretValue | None,
        failure: CaptureError,
    ) -> None:
        if connection is None or secret_reference is None:
            return
        try:
            await self._secrets.disable_for(connection, secret_reference, access_token)
        except Exception as cleanup_error:
            failure.cleanup_required = True
            raise CaptureError(
                "secure capture failed and the stored version requires reconciliation",
                secret_reference,
                cleanup_required=True,
            ) from cleanup_error

    async def _checkpoint(self, checkpoint: PageCheckpoint) -> None:
        if not fnmatch.fnmatchcase(self._page.url, checkpoint.url_pattern):
            raise CaptureError("secure capture URL checkpoint changed")
        for text in checkpoint.required_text:
            if await self._page.get_by_text(text, exact=True).count() == 0:
                raise CaptureError(f"secure capture checkpoint text is missing: {text}")
        for text in checkpoint.forbidden_text:
            if await self._page.get_by_text(text, exact=True).count() > 0:
                raise CaptureError(f"secure capture found forbidden text: {text}")

    async def _clear_clipboard(self) -> None:
        await self._page.evaluate(
            """async () => {
              try { await navigator.clipboard.writeText(''); } catch (_) {}
            }"""
        )


def _resource_allowed(resource: str, allowed: tuple[str, ...]) -> bool:
    return any(
        resource == boundary or resource.startswith(boundary.rstrip("/") + "/")
        for boundary in allowed
    )


async def _provider_display_name(driver: BrowserDriver, field: SecureField) -> str | None:
    if field.provider_display_name_selector is None:
        return None
    value = await _read(await driver.locator(field.provider_display_name_selector))
    if not value or len(value) > 256:
        raise CaptureError("declared provider display name is empty or invalid")
    return value


async def _read(locator: Locator, attribute: str = "text") -> str:
    if attribute == "href":
        value = await locator.get_attribute("href")
        return value.strip() if value else ""
    tag = await locator.evaluate("element => element.tagName.toLowerCase()")
    if attribute == "value" or (attribute == "text" and tag in {"input", "textarea"}):
        return await locator.input_value()
    value = await locator.text_content()
    return value.strip() if value else ""


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CaptureError(f"secret store returned no {label}")
    return value


_MASK_SCRIPT = """
(element, values) => {
  const mask = values.mask;
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    element.value = mask;
    element.removeAttribute('value');
  } else {
    element.textContent = mask;
  }
  element.dataset.kinuCapture = 'masked';
  element.setAttribute('aria-label', 'Credential stored securely');
  const observer = new MutationObserver(() => {
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      if (element.value !== mask) element.value = mask;
      element.removeAttribute('value');
    } else if (element.textContent !== mask) {
      element.textContent = mask;
    }
  });
  const options = {attributes: true, childList: true, characterData: true, subtree: true};
  observer.observe(element, options);
  return element.outerHTML;
}
"""

_EXPOSURE_SCRIPT = """
(secret) => {
  if (!secret) return true;
  if ((document.body?.innerText || '').includes(secret)) return true;
  for (const item of document.querySelectorAll('input, textarea')) {
    if ((item.value || '').includes(secret)) return true;
    if ((item.getAttribute('value') || '').includes(secret)) return true;
  }
  return false;
}
"""
