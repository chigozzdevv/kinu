from playwright.async_api import Page


async def snapshot(page: Page, secret: str) -> bytes:
    session = await page.context.new_cdp_session(page)
    origin = page.url
    try:
        await session.send("Emulation.setScriptExecutionDisabled", {"value": True})
        await page.evaluate(
            r"""secret => {
              const tokens = /\b(?:re_|sk_|rk_)[A-Za-z0-9_-]{24,}|\bBearer\s+[A-Za-z0-9._-]{16,}/;
              for (const element of document.querySelectorAll('body *')) {
                const values = Array.from(element.attributes, item => item.value);
                if (!element.children.length) values.push(element.textContent || '');
                for (const pseudo of ['::before', '::after']) {
                  values.push(getComputedStyle(element, pseudo).content || '');
                }
                if (values.some(value => value.includes(secret) || tokens.test(value))) {
                  element.setAttribute('data-kinu-evidence-mask', '');
                }
              }
            }""",
            secret,
        )
        masks = page.locator(
            "input, textarea, [contenteditable], iframe, canvas, video, img, svg, object, embed, "
            "[data-kinu-capture], [data-kinu-evidence-mask]"
        )
        dialog = page.locator('[data-kinu-capture="masked"]').locator(
            "xpath=ancestor::*[@role='dialog' or self::dialog][1]"
        )
        target = dialog if await dialog.count() == 1 and await dialog.is_visible() else page
        content = await target.screenshot(
            type="png", animations="disabled", mask=[masks], mask_color="#dce1e6"
        )
        if page.url != origin:
            raise ValueError("Capture page changed during evidence recording")
        return content
    finally:
        await session.detach()
