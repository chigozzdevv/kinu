import asyncio
import fnmatch
import secrets
from collections.abc import Iterable
from urllib.parse import urlparse

from contracts import (
    BrowserAction,
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    PageCheckpoint,
    PlaybookStep,
    Selector,
    SelectorKind,
    StepOutput,
)
from core.errors import ResourceConflictError
from playwright.async_api import ElementHandle, Locator, Page, Request, Route
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser.url import metadata_url


class AuthenticationRequiredError(RuntimeError):
    pass


class BrowserDriver:
    def __init__(self, page: Page, policy: BrowserPolicy) -> None:
        self._page = page
        self._policy = policy
        self._blocked_egress = False
        self._model_targets: dict[str, ElementHandle] = {}
        self._model_control_targets: dict[str, str] = {}

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
        await self._page.context.route("**/*", self._route)

    async def _settle(self) -> None:
        for _ in range(50):
            try:
                text = (await self._page.locator("body").inner_text())[:500]
            except Exception:
                text = ""
            if (
                "Vercel Security Checkpoint" not in text
                and "verifying your browser" not in text.lower()
            ):
                return
            await asyncio.sleep(0.5)

    async def execute(self, action: BrowserAction) -> None:
        self._blocked_egress = False
        if action.kind not in self._policy.allowed_actions:
            raise ResourceConflictError("browser action is excluded by policy")
        if action.kind is BrowserActionKind.NAVIGATE:
            assert action.url is not None
            self.validate_url(action.url)
            await self._page.goto(action.url, wait_until="load")
            await self._settle()
        elif action.kind is BrowserActionKind.CLICK:
            await self._act_on_target(action, "click")
        elif action.kind is BrowserActionKind.TYPE:
            assert action.value is not None
            await self._act_on_target(action, "fill", action.value)
        elif action.kind is BrowserActionKind.SELECT:
            assert action.value is not None
            if action.control_id:
                await self._select_visual_option(action.control_id, action.value)
            else:
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

    async def control_label(self, selector: Selector) -> str:
        target = await self.locator(selector)
        label = await target.evaluate("""element => {
          const clone = element.cloneNode(true);
          clone.querySelectorAll('kbd, svg, [aria-hidden="true"]').forEach(item => item.remove());
          return (element.getAttribute('aria-label') || clone.textContent || '').trim();
        }""")
        if not isinstance(label, str) or not 1 <= len(label) <= 160:
            raise ResourceConflictError("Approved browser control has no bounded label")
        return " ".join(label.split())

    async def evidence_screenshot(self, session: BrowserSession) -> bytes:
        if (
            session.status is not BrowserStatus.RUNNING
            or session.model_paused
            or session.recording_paused
        ):
            raise ResourceConflictError("Browser evidence is blocked during private capture")
        self._check_authentication()
        self.validate_url(self.url)
        await self._page.evaluate(r"""() => {
          const secrets = [
            /\b(?:re_|sk_|rk_)[A-Za-z0-9_-]{24,}/,
            /\bBearer\s+[A-Za-z0-9._-]{16,}/,
            /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/
          ];
          for (const element of document.querySelectorAll('body *')) {
            const sensitive = secrets.some(secret => secret.test(element.textContent || ''));
            if (!element.children.length && sensitive) {
              element.setAttribute('data-kinu-evidence-mask', '');
            }
          }
        }""")
        masks = self._page.locator(
            "input, textarea, [contenteditable], iframe, canvas, video, img, object, embed, "
            "[data-kinu-evidence-mask]"
        )
        try:
            return await self._page.screenshot(
                type="png", animations="disabled", mask=[masks], mask_color="#dce1e6"
            )
        finally:
            await self._page.locator("[data-kinu-evidence-mask]").evaluate_all(
                "elements => elements.forEach(element => "
                "element.removeAttribute('data-kinu-evidence-mask'))"
            )

    async def model_screenshot(
        self,
        session: BrowserSession,
        selector: Selector | None = None,
        *,
        include_text: bool = False,
        trusted_text: Iterable[str] = (),
    ) -> tuple[bytes, dict[str, str], str | None]:
        """Capture a redacted page and retain short-lived handles for visible controls."""
        if (
            session.status is not BrowserStatus.RUNNING
            or session.model_paused
            or session.recording_paused
        ):
            raise ResourceConflictError("Browser model view is blocked during private capture")
        self._check_authentication()
        self.validate_url(self.url)
        await self.clear_model_targets()
        handle = None
        if selector is not None:
            target_locator = await self.locator(selector)
            handle = await target_locator.element_handle()
        if selector and handle is None:
            raise ResourceConflictError("Approved browser control is unavailable")
        cdp = await self._page.context.new_cdp_session(self._page)
        origin = self.url
        marker = secrets.token_hex(16)
        target_attribute = f"data-kinu-target-{marker}"
        trusted = tuple(
            value for value in trusted_text if isinstance(value, str) and 0 < len(value) <= 256
        )
        try:
            await cdp.send("Emulation.setScriptExecutionDisabled", {"value": True})
            if handle is not None:
                await handle.evaluate(
                    "(element, attribute) => element.setAttribute(attribute, 'fixed')",
                    target_attribute,
                )
            tree = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
            pending = [tree["root"]]
            while pending:
                node = pending.pop()
                if any(
                    root.get("shadowRootType") != "user-agent"
                    for root in node.get("shadowRoots", ())
                ):
                    raise ResourceConflictError("Page contains unsupported shadow content")
                pending.extend(node.get("children", ()))
            result = await self._page.evaluate(
                r"""values => {
              const {marker, includeText, trustedValues} = values;
              const target = document.querySelector('[data-kinu-target-' + marker + '="fixed"]');
              if (Array.from(document.querySelectorAll('*')).some(e =>
                e.shadowRoot || e.localName.includes('-') || e.hasAttribute('is')))
                throw new Error('Page contains unsupported shadow content');
              const safe = new Set(['API keys', 'Create API key', 'Create key', 'Add',
                'Name', 'Permission', 'Domain', 'Sending access', 'More actions',
                'Delete API key', 'Delete API Key', 'Delete', 'Revoke', 'Revoke key', 'Retire key',
                'Cancel', 'Confirm', 'Save', 'Continue', 'Close', 'Secret', 'API key',
                'Secret key', 'Key ID', 'ID', 'Token', 'Create API Key', 'Copy']);
              const trusted = new Set(trustedValues || []);
              const safeWords = new Set(['api', 'key', 'keys', 'secret', 'token', 'name',
                'permission', 'permissions', 'domain', 'domains', 'sending', 'access', 'scope',
                'scopes', 'create', 'new', 'add', 'generate', 'delete', 'revoke', 'retire',
                'more', 'actions', 'cancel', 'confirm', 'save', 'continue', 'close', 'copy',
                'id', 'identifier', 'type', 'select', 'choose', 'value', 'expires', 'expiration',
                'never', 'restricted', 'full', 'read', 'write', 'all', 'only']);
              const safeLabel = value => {
                const text = (value || '').trim();
                if (!text || text.length > 80) return false;
                if (/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i.test(text)
                    || /\b(?:re_|sk_|rk_|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]{12,}/i.test(text)
                    || /\bBearer\s+[A-Za-z0-9._-]{16,}/i.test(text)
                    || /\b[0-9a-f]{8}-[0-9a-f-]{27,}\b/i.test(text)
                    || /\b[A-Za-z0-9_-]{32,}\b/.test(text)) return false;
                if (safe.has(text) || trusted.has(text)) return true;
                const words = text.toLowerCase().match(/[a-z]+/g) || [];
                return words.length > 0 && words.every(word => safeWords.has(word));
              };
              const visible = e => {
                const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0
                  && r.top < innerHeight && r.left < innerWidth
                  && s.visibility === 'visible' && s.display !== 'none';
              };
              const interactive = Array.from(document.querySelectorAll(
                'button, a[href], input:not([type=hidden]), select, textarea, '
                + '[role=button], [role=menuitem], [role=textbox], [role=combobox]'
              )).filter(visible);
              const interactiveLabel = e => {
                const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
                  .map(id => document.getElementById(id)?.textContent || '').join(' ');
                const labels = Array.from(e.labels || [])
                  .map(label => label.textContent || '').join(' ');
                return (e.getAttribute('aria-label') || labelled || labels || e.textContent || '')
                  .trim().toLowerCase();
              };
              const priority = e => {
                const label = interactiveLabel(e);
                const dialog = e.closest('dialog, [role=dialog], [aria-modal=true]') ? 1000 : 0;
                const keyAction = new RegExp(
                  'api key|secret|token|key id|identifier|create|generate|add|revoke|delete|'
                  + 'name|permission|domain'
                ).test(label) ? 100 : 0;
                const input = ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.tagName) ? 10 : 0;
                return dialog + keyAction + input;
              };
              interactive.sort((left, right) => priority(right) - priority(left));
              const textual = includeText ? Array.from(document.querySelectorAll('body *'))
                .filter(e => !e.children.length && (e.textContent || '').trim().length > 0
                  && visible(e) && !['SCRIPT', 'STYLE', 'SVG', 'PATH'].includes(e.tagName))
                .sort((left, right) => {
                  const score = element => {
                    const text = (element.textContent || '').trim();
                    const words = text.toLowerCase();
                    return (trusted.has(text) ? 100 : 0)
                      + (/\b(api|key|secret|token|id|identifier|created|copy)\b/.test(words)
                        ? 40 : 0)
                      + (/^[A-Za-z0-9:_-]{12,}$/.test(text) ? 20 : 0)
                      + (safeLabel(text) ? 10 : 0);
                  };
                  return score(right) - score(left);
                }) : [];
              const all = Array.from(new Set(includeText
                ? [...interactive.slice(0, 8), ...textual]
                : interactive));
              if (target && !all.includes(target)) all.unshift(target);
              const candidates = all.slice(0, 12);
              if (target && !candidates.includes(target)) candidates[11] = target;
              if (candidates.some(e => e.getBoundingClientRect().width < 1))
                throw new Error('Browser control is not visible');
              if (document.querySelector('[data-kinu-model-safe], [data-kinu-model-pseudo]')
                  || document.getElementById('kinu-model-view-style')
                  || document.getElementById('kinu-model-markers')
                  || window.__kinuModelStyles) throw new Error('Model view marker collision');
              const saved = [];
              window.__kinuModelStyles = {marker, saved};
              for (const e of document.querySelectorAll('*')) {
                saved.push([e, e.getAttribute('style')]);
                e.style.setProperty('color', 'transparent', 'important');
                e.style.setProperty('-webkit-text-fill-color', 'transparent', 'important');
                e.style.setProperty('-webkit-text-stroke', '0', 'important');
                e.style.setProperty('text-shadow', 'none', 'important');
                e.style.setProperty('text-decoration-color', '#fff', 'important');
                e.style.setProperty('background-image', 'none', 'important');
                e.style.setProperty('border-image-source', 'none', 'important');
                e.style.setProperty('mask-image', 'none', 'important');
                e.style.setProperty('-webkit-mask-image', 'none', 'important');
                e.style.setProperty('clip-path', 'none', 'important');
                e.style.setProperty('background-color', '#fff', 'important');
                e.style.setProperty('border-color', '#c7cdd3', 'important');
                e.style.setProperty('outline-color', '#c7cdd3', 'important');
                e.style.setProperty('filter', 'none', 'important');
                e.style.setProperty('mix-blend-mode', 'normal', 'important');
                e.style.setProperty('box-shadow', 'none', 'important');
                if (e.children.length === 0 && safeLabel(e.textContent || '')) {
                  e.setAttribute('data-kinu-model-safe', '');
                  e.style.setProperty('color', '#151515', 'important');
                  e.style.setProperty('-webkit-text-fill-color', '#151515', 'important');
                }
              }
              for (const e of document.querySelectorAll('*')) {
                for (const pseudo of ['::before', '::after', '::marker']) {
                  const content = getComputedStyle(e, pseudo).content;
                  if (content && content !== 'none' && content !== 'normal')
                    e.setAttribute('data-kinu-model-pseudo', '');
                }
              }
              const style = document.createElement('style');
              style.id = 'kinu-model-view-style';
              style.textContent = `
                * { color: transparent !important; -webkit-text-fill-color: transparent !important;
                    text-shadow: none !important; background-image: none !important;
                    text-decoration-color: #fff !important; border-image-source: none !important;
                    mask-image: none !important; -webkit-mask-image: none !important;
                    clip-path: none !important;
                    background-color: #fff !important; border-color: #c7cdd3 !important;
                    outline-color: #c7cdd3 !important; filter: none !important;
                    mix-blend-mode: normal !important; box-shadow: none !important;
                    caret-color: transparent !important; }
                *::before, *::after { content: none !important; background-image: none !important; }
                [data-kinu-model-safe] { color: #151515 !important; }
                #kinu-model-markers, #kinu-model-markers * { visibility: visible !important;
                  color: white !important; -webkit-text-fill-color: white !important;
                  font: bold 12px sans-serif !important; }
              `;
              document.documentElement.appendChild(style);
              const overlay = document.createElement('div');
              overlay.id = 'kinu-model-markers';
              overlay.style.cssText = 'position:fixed;inset:0;pointer-events:none;'
                + 'z-index:2147483647';
              document.documentElement.appendChild(overlay);
              const controls = {};
              candidates.forEach((e, index) => {
                e.setAttribute('data-kinu-target-' + marker, String(index + 1));
                const id = 'control_' + (index + 1), box = e.getBoundingClientRect();
                const clone = e.cloneNode(true);
                clone.querySelectorAll('kbd, svg, [aria-hidden=true]').forEach(n => n.remove());
                const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
                  .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
                const labels = Array.from(e.labels || [])
                  .map(label => label.textContent || '').join(' ').trim();
                const text = (e.getAttribute('aria-label') || labelled || labels
                  || clone.textContent || '').trim();
                const rawKind = e.getAttribute('role') || e.tagName.toLowerCase();
                const kind = ['button', 'menuitem', 'textbox', 'combobox', 'input',
                  'select', 'textarea', 'a'].includes(rawKind) ? rawKind : 'control';
                controls[id] = kind + (safeLabel(text) ? ': ' + text : '');
                const badge = document.createElement('span');
                badge.textContent = String(index + 1);
                badge.style.cssText = 'position:fixed;left:' + Math.max(0, box.left)
                  + 'px;top:' + Math.max(0, box.top - 20) + 'px;background:#111;'
                  + 'padding:3px 5px;border-radius:3px;';
                overlay.appendChild(badge);
              });
              return {
                controls,
                approved: target ? 'control_' + (candidates.indexOf(target) + 1) : null
              };
            }""",
                {
                    "marker": marker,
                    "includeText": include_text,
                    "trustedValues": trusted,
                },
            )
            masks = self._page.locator(
                "input, textarea, select, [contenteditable], iframe, canvas, video, img, svg, "
                "object, embed, [data-kinu-model-pseudo]"
            )
            image = await self._page.screenshot(
                type="png", animations="disabled", mask=[masks], mask_color="#dce1e6"
            )
            if self.url != origin:
                raise ResourceConflictError("Browser page changed during visual observation")
            for index, control_id in enumerate(result["controls"], start=1):
                locator = self._page.locator(f'[{target_attribute}="{index}"]')
                target_handle = await locator.element_handle()
                if target_handle is None:
                    raise ResourceConflictError("Browser visual target changed during observation")
                target_id = secrets.token_urlsafe(24)
                self._model_targets[target_id] = target_handle
                self._model_control_targets[control_id] = target_id
            return image, result["controls"], result["approved"]
        except Exception:
            await self.clear_model_targets()
            raise
        finally:
            try:
                await self._page.evaluate(
                    """marker => {
                  if (window.__kinuModelStyles?.marker !== marker) return;
                  document.getElementById('kinu-model-view-style')?.remove();
                  document.getElementById('kinu-model-markers')?.remove();
                  for (const [element, original] of window.__kinuModelStyles.saved) {
                    if (original === null) element.removeAttribute('style');
                    else element.setAttribute('style', original);
                    element.removeAttribute('data-kinu-model-safe');
                    element.removeAttribute('data-kinu-model-pseudo');
                  }
                  for (const element of document.querySelectorAll(
                    '[data-kinu-target-' + marker + ']'
                  ))
                    element.removeAttribute('data-kinu-target-' + marker);
                  delete window.__kinuModelStyles;
                }""",
                    marker,
                )
            finally:
                await cdp.detach()
                if handle is not None:
                    await handle.dispose()

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
        await self._page.goto(url, wait_until="load")
        await self._settle()

    async def setup_enter(self, url: str) -> None:
        self._check_authentication()
        self.validate_url(self.url)
        self.validate_url(url)
        links = self._page.locator("a[href]")
        matches = await links.evaluate_all(
            "(elements, target) => elements.flatMap((element, index) => "
            "element.href === target ? [index] : [])",
            url,
        )
        if len(matches) != 1 or not await links.nth(matches[0]).is_visible():
            raise ResourceConflictError("Approved key-page link is not uniquely visible")
        await self.setup_navigate(url)

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
        if step.checkpoint is not None:
            await self.validate_checkpoint(step.checkpoint)
        else:
            self._check_blocked_egress()
            self._check_authentication()
            self.validate_url(self._page.url)
        for selector in step.selectors:
            await self.locator(selector)
        for condition in step.conditions:
            locator = await self.locator(condition.selector)
            value = (
                await locator.input_value()
                if condition.attribute == "value"
                else await locator.inner_text()
            )
            if value.strip() != condition.expected:
                raise ResourceConflictError("browser control differs from its approved value")

    async def validate_checkpoint(self, checkpoint: PageCheckpoint) -> None:
        self.validate_current_page()
        if not fnmatch.fnmatchcase(self._page.url, checkpoint.url_pattern):
            raise ResourceConflictError("browser URL does not match the approved checkpoint")
        await self._validate_text(checkpoint.required_text, checkpoint.forbidden_text)

    def validate_current_page(self) -> None:
        self._check_blocked_egress()
        self._check_authentication()
        self.validate_url(self._page.url)

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
        if require_unique and (count == 0 or (count == 1 and not await locator.is_visible())):
            try:
                await locator.wait_for(state="visible", timeout=_CHECKPOINT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                raise ResourceConflictError("approved selector did not become visible") from None
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
        count = await option.count()
        if count == 0 or (count == 1 and not await option.is_visible()):
            try:
                await option.wait_for(state="visible", timeout=_CHECKPOINT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                raise ResourceConflictError(
                    "approved select option did not become visible"
                ) from None
        if await option.count() != 1 or not await option.is_visible():
            raise ResourceConflictError("approved select option is not uniquely visible")
        await option.click()

    async def _act_on_target(
        self, action: BrowserAction, operation: str, value: str | None = None
    ) -> None:
        if action.control_id:
            handle = await self.take_model_target(action.control_id)
            try:
                if not await handle.is_visible():
                    raise ResourceConflictError("Selected browser control is no longer visible")
                if operation == "click":
                    await handle.click()
                else:
                    assert value is not None
                    await handle.fill(value)
            finally:
                await handle.dispose()
            return
        target = await self.locator(_selector(action))
        if operation == "click":
            await target.click()
        else:
            assert value is not None
            await target.fill(value)

    async def _select_visual_option(self, control_id: str, value: str) -> None:
        handle = await self.take_model_target(control_id)
        try:
            if not await handle.is_visible():
                raise ResourceConflictError("Selected browser control is no longer visible")
            if (await handle.evaluate("element => element.tagName.toLowerCase()")) == "select":
                await handle.select_option(label=value)
                return
            await handle.click()
            option = self._page.get_by_role("option", name=value, exact=True)
            count = await option.count()
            if count == 0 or (count == 1 and not await option.is_visible()):
                try:
                    await option.wait_for(state="visible", timeout=_CHECKPOINT_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    raise ResourceConflictError(
                        "approved select option did not become visible"
                    ) from None
            if await option.count() != 1 or not await option.is_visible():
                raise ResourceConflictError("approved select option is not uniquely visible")
            await option.click()
        finally:
            await handle.dispose()

    async def take_model_target(self, target_id: str) -> ElementHandle:
        target = self._model_targets.pop(target_id, None)
        if target is None:
            raise ResourceConflictError("Browser visual target expired")
        for control_id, current in tuple(self._model_control_targets.items()):
            if current == target_id:
                self._model_control_targets.pop(control_id, None)
        return target

    async def claim_model_target(self, control_id: str) -> str:
        target_id = self._model_control_targets.get(control_id)
        if target_id is None or target_id not in self._model_targets:
            raise ResourceConflictError("Builder selected a control outside the screenshot")
        return target_id

    async def clear_model_targets(self, keep: Iterable[str] = ()) -> None:
        retained = set(keep)
        for target_id in tuple(self._model_targets):
            if target_id in retained:
                continue
            target = self._model_targets.pop(target_id)
            await target.dispose()
        self._model_control_targets = {
            control_id: target_id
            for control_id, target_id in self._model_control_targets.items()
            if target_id in retained and target_id in self._model_targets
        }

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

_CHECKPOINT_TIMEOUT_MS = 30_000
