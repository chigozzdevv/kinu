import fnmatch
from collections.abc import Iterable
from urllib.parse import urlparse

from contracts import (
    BrowserAction,
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    PlaybookStep,
    Selector,
    SelectorKind,
    StepOutput,
)
from core.errors import ResourceConflictError
from playwright.async_api import Locator, Page, Request, Route
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser.url import metadata_url


class AuthenticationRequiredError(RuntimeError):
    pass


class BrowserDriver:
    def __init__(self, page: Page, policy: BrowserPolicy) -> None:
        self._page = page
        self._policy = policy
        self._blocked_egress = False

    @property
    def url(self) -> str:
        return self._page.url

    @property
    def metadata_url(self) -> str:
        self.validate_url(self._page.url)
        return metadata_url(self._page.url)

    @property
    def domains(self) -> tuple[str, ...]:
        return self._policy.allowed_domains

    async def enforce_egress(self) -> None:
        await self._page.route("**/*", self._route)

    async def execute(self, action: BrowserAction) -> None:
        self._blocked_egress = False
        if action.kind not in self._policy.allowed_actions:
            raise ResourceConflictError("browser action is excluded by policy")
        if action.kind is BrowserActionKind.NAVIGATE:
            assert action.url is not None
            self.validate_url(action.url)
            await self._page.goto(action.url, wait_until="domcontentloaded")
        elif action.kind is BrowserActionKind.CLICK:
            await (await self.locator(_selector(action))).click()
        elif action.kind is BrowserActionKind.TYPE:
            assert action.value is not None
            await (await self.locator(_selector(action))).fill(action.value)
        elif action.kind is BrowserActionKind.SELECT:
            assert action.value is not None
            await self._select_option(_selector(action), action.value)
        elif action.kind is BrowserActionKind.SCROLL:
            value = _bounded_integer(action.value, -2000, 2000)
            await self._page.mouse.wheel(0, value)
        elif action.kind is BrowserActionKind.KEY:
            if action.value not in _KEYS:
                raise ResourceConflictError("browser key is not in the safe key allowlist")
            await self._page.keyboard.press(action.value)
        elif action.kind is BrowserActionKind.WAIT:
            seconds = _bounded_integer(action.value, 0, 10)
            await self._page.wait_for_timeout(seconds * 1000)
        else:
            raise ResourceConflictError(f"browser action {action.kind.value} is unsupported")
        await self._validate_checkpoint(action)

    async def screenshot(
        self,
        session: BrowserSession,
        masked_selectors: Iterable[Selector] = (),
    ) -> bytes:
        if session.model_paused or session.recording_paused:
            raise ResourceConflictError("browser screenshot barriers are armed")
        masks = [
            await self.locator(selector, require_unique=False) for selector in masked_selectors
        ]
        return await self._page.screenshot(type="png", animations="disabled", mask=masks)

    async def live_screenshot(
        self,
        session: BrowserSession,
        masked_selectors: Iterable[Selector] = (),
    ) -> bytes:
        if session.status is not BrowserStatus.TAKEOVER or not session.model_paused:
            raise ResourceConflictError("live screenshot requires supervised takeover")
        masks = [
            await self.locator(selector, require_unique=False) for selector in masked_selectors
        ]
        return await self._page.screenshot(type="png", animations="disabled", mask=masks)

    async def setup_screenshot(self) -> bytes:
        sensitive = self._page.locator(_SETUP_SENSITIVE_SELECTOR)
        return await self._page.screenshot(
            type="png",
            animations="disabled",
            mask=[sensitive],
            mask_color="#3b3f46",
        )

    async def setup_navigate(self, url: str) -> None:
        self.validate_url(url)
        await self._page.goto(url, wait_until="domcontentloaded")

    async def setup_click(self, x: int, y: int) -> None:
        viewport = self._page.viewport_size
        if viewport is None:
            raise ResourceConflictError("browser setup has no viewport")
        normalised_x = _bounded_integer(str(x), 0, 1000)
        normalised_y = _bounded_integer(str(y), 0, 1000)
        await self._page.mouse.click(
            normalised_x / 1000 * viewport["width"],
            normalised_y / 1000 * viewport["height"],
        )

    async def setup_scroll(self, value: int) -> None:
        await self._page.mouse.wheel(0, _bounded_integer(str(value), -2000, 2000))

    async def setup_key(self, value: str) -> None:
        if value not in _KEYS:
            raise ResourceConflictError("browser key is not in the safe key allowlist")
        await self._page.keyboard.press(value)

    async def setup_type(self, value: str) -> None:
        if not value or len(value) > 256:
            raise ResourceConflictError("browser setup input has an invalid length")
        await self._page.keyboard.insert_text(value)

    async def validate_coordinate(self, selector: Selector, x: int, y: int) -> None:
        if not 0 <= x <= 999 or not 0 <= y <= 999:
            raise ResourceConflictError("model coordinate is outside the normalised viewport")
        locator = await self.locator(selector)
        box = await locator.bounding_box()
        viewport = self._page.viewport_size
        if box is None or viewport is None:
            raise ResourceConflictError("approved control has no visible viewport bounds")
        actual_x = x / 1000 * viewport["width"]
        actual_y = y / 1000 * viewport["height"]
        inside = (
            box["x"] <= actual_x <= box["x"] + box["width"]
            and box["y"] <= actual_y <= box["y"] + box["height"]
        )
        if not inside:
            raise ResourceConflictError("model coordinate does not hit the approved control")

    async def validate_step(self, step: PlaybookStep) -> None:
        if step.checkpoint is None:
            raise ResourceConflictError("browser step has no deterministic checkpoint")
        self._check_blocked_egress()
        self._check_authentication()
        self.validate_url(self._page.url)
        if not fnmatch.fnmatchcase(self._page.url, step.checkpoint.url_pattern):
            raise ResourceConflictError("browser URL does not match the approved checkpoint")
        for selector in step.selectors:
            await self.locator(selector)
        await self._validate_text(step.checkpoint.required_text, step.checkpoint.forbidden_text)

    async def extract(self, outputs: tuple[StepOutput, ...]) -> dict[str, str]:
        values: dict[str, str] = {}
        for output in outputs:
            locator = await self.locator(output.selector)
            raw: str | None
            if output.attribute == "value":
                raw = await locator.input_value()
            else:
                raw = await locator.text_content()
            if not isinstance(raw, str) or not raw.strip():
                raise ResourceConflictError(f"browser output {output.name} is empty")
            values[output.name] = raw.strip()
        return values

    async def locator(self, selector: Selector, require_unique: bool = True) -> Locator:
        if selector.kind is SelectorKind.ROLE:
            locator = self._page.get_by_role(
                selector.value,  # type: ignore[arg-type]
                name=selector.name,
                exact=selector.exact,
            )
        elif selector.kind is SelectorKind.LABEL:
            locator = self._page.get_by_label(selector.value, exact=selector.exact)
        elif selector.kind is SelectorKind.FIELD:
            locator = await self._field_locator(selector)
        elif selector.kind is SelectorKind.TEXT:
            locator = self._page.get_by_text(selector.value, exact=selector.exact)
        elif selector.kind is SelectorKind.TEST_ID:
            locator = self._page.get_by_test_id(selector.value)
        elif selector.kind is SelectorKind.CSS:
            locator = self._page.locator(selector.value)
        else:
            raise ResourceConflictError(f"selector kind {selector.kind.value} is unsupported")
        count = await locator.count()
        if require_unique and count != 1:
            raise ResourceConflictError(
                f"approved selector resolved to {count} elements instead of one"
            )
        if require_unique and not await locator.is_visible():
            raise ResourceConflictError("approved selector is not visible")
        return locator

    async def _field_locator(self, selector: Selector) -> Locator:
        field_xpath = (
            "xpath=following::*[self::input or self::select or self::textarea or self::button][1]"
        )
        labeled = self._page.get_by_label(selector.value, exact=selector.exact)
        if await labeled.count() == 1:
            return labeled
        label = self._page.locator("label").filter(has_text=selector.value)
        if await label.count() == 1:
            return label.locator(field_xpath)
        text = self._page.get_by_text(selector.value, exact=selector.exact)
        if await text.count() == 1:
            return text.locator(field_xpath)
        return labeled

    async def _select_option(self, selector: Selector, value: str) -> None:
        field = await self.locator(selector)
        tag_name = await field.evaluate("element => element.tagName.toLowerCase()")
        if tag_name == "select":
            await field.select_option(label=value)
            return
        await field.click()
        option = self._page.get_by_role("option", name=value, exact=True)
        if await option.count() != 1:
            option = self._page.get_by_text(value, exact=True)
        if await option.count() != 1 or not await option.is_visible():
            raise ResourceConflictError("approved select option is not uniquely visible")
        await option.click()

    async def same_element(self, left: Selector, right: Selector) -> bool:
        left_locator = await self.locator(left)
        right_locator = await self.locator(right, require_unique=False)
        count = await right_locator.count()
        if count == 0:
            return False
        if count != 1 or not await right_locator.is_visible():
            raise ResourceConflictError("protected selector does not resolve uniquely")
        left_handle = await left_locator.element_handle()
        right_handle = await right_locator.element_handle()
        if left_handle is None or right_handle is None:
            raise ResourceConflictError("browser control could not be resolved")
        try:
            return bool(
                await left_handle.evaluate(
                    "(element, protectedElement) => element === protectedElement",
                    right_handle,
                )
            )
        finally:
            await left_handle.dispose()
            await right_handle.dispose()

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ResourceConflictError("browser navigation requires a credential-free HTTPS URL")
        hostname = parsed.hostname.lower().rstrip(".")
        if not any(_domain(hostname, value) for value in self._policy.allowed_domains):
            raise AuthenticationRequiredError(f"provider session left the allowlist at {hostname}")

    async def _route(self, route: Route, request: Request) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"about", "blob", "data"}:
            await route.continue_()
            return
        try:
            self.validate_url(request.url)
        except AuthenticationRequiredError:
            if request.is_navigation_request() and request.frame == self._page.main_frame:
                self._blocked_egress = True
            await route.abort("blockedbyclient")
            return
        except ResourceConflictError:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _validate_checkpoint(self, action: BrowserAction) -> None:
        self._check_blocked_egress()
        self._check_authentication()
        self.validate_url(self._page.url)
        if action.expected_url and not fnmatch.fnmatchcase(self._page.url, action.expected_url):
            raise ResourceConflictError("browser URL does not match the approved checkpoint")
        await self._validate_text(action.expected_text, action.forbidden_text)

    def _check_authentication(self) -> None:
        pattern = self._policy.login_url_pattern
        if pattern and (
            fnmatch.fnmatchcase(self._page.url, pattern)
            or fnmatch.fnmatchcase(metadata_url(self._page.url), pattern)
        ):
            raise AuthenticationRequiredError("provider session landed on the login page")

    def _check_blocked_egress(self) -> None:
        if self._blocked_egress:
            raise AuthenticationRequiredError("provider session redirected off the allowlist")

    async def _validate_text(self, required: tuple[str, ...], forbidden: tuple[str, ...]) -> None:
        for text in required:
            try:
                await self._page.get_by_text(text, exact=True).first.wait_for(
                    state="visible",
                    timeout=_CHECKPOINT_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError as error:
                self._check_blocked_egress()
                self._check_authentication()
                self.validate_url(self._page.url)
                raise ResourceConflictError(
                    f"browser checkpoint text is missing: {text}"
                ) from error
        for text in forbidden:
            if await self._page.get_by_text(text, exact=True).count() != 0:
                raise ResourceConflictError(f"browser checkpoint contains forbidden text: {text}")


def _selector(action: BrowserAction) -> Selector:
    if action.selector is None:
        raise ResourceConflictError("browser action requires an approved selector")
    return action.selector


def _domain(hostname: str, pattern: str) -> bool:
    expected = pattern.lower().rstrip(".")
    if expected.startswith("*."):
        suffix = expected[2:]
        return hostname.endswith("." + suffix) and hostname != suffix
    return hostname == expected


def _bounded_integer(value: str | None, minimum: int, maximum: int) -> int:
    try:
        result = int(value or "")
    except ValueError as error:
        raise ResourceConflictError("browser action requires an integer value") from error
    if not minimum <= result <= maximum:
        raise ResourceConflictError("browser action integer is outside its safe range")
    return result


_KEYS = frozenset(
    {
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "Backspace",
        "Enter",
        "Escape",
        "Tab",
    }
)

_SETUP_SENSITIVE_SELECTOR = ", ".join(
    (
        'input[type="password"]',
        'input[autocomplete="current-password"]',
        'input[autocomplete="new-password"]',
        'input[autocomplete="one-time-code"]',
        'input[name*="secret" i]',
        'input[id*="secret" i]',
        'input[name*="token" i]',
        'input[id*="token" i]',
        'input[name*="api-key" i]',
        'input[id*="api-key" i]',
        'textarea[name*="secret" i]',
        'textarea[id*="secret" i]',
        'textarea[name*="token" i]',
        'textarea[id*="token" i]',
        'textarea[name*="api-key" i]',
        'textarea[id*="api-key" i]',
    )
)

_CHECKPOINT_TIMEOUT_MS = 10_000
