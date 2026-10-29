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
        content = await page.screenshot(
            type="png", animations="disabled", mask=[masks], mask_color="#3b3f46"
        )
        if page.url != origin:
            raise ValueError("Capture page changed during evidence recording")
        return content
    finally:
        await session.detach()
