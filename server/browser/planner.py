import asyncio
import json
from html import escape
from typing import Any, Protocol

from core.errors import ResourceConflictError
from playwright.async_api import async_playwright
from pydantic import BaseModel, ConfigDict, Field
from stagehand import (
    LLMGenerateInput,
    LLMStructuredGenerateParams,
    LLMStructuredGenerateResult,
    LLMTextContent,
    Stagehand,
    local_browser,
)


class Navigation(Protocol):
    async def navigate(self, organisation_id: str, value: dict[str, Any]) -> dict[str, Any]: ...


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    control: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=320)


class ControlPlanner(Protocol):
    async def choose(self, org: str, objective: str, controls: dict[str, str]) -> Choice: ...


class Planner:
    def __init__(self, agent: Navigation, executable: str | None = None) -> None:
        self.agent, self.executable = agent, executable

    async def choose(self, org: str, objective: str, controls: dict[str, str]) -> Choice:
        if (
            not controls
            or len(controls) > 12
            or any(len(value) > 160 for value in controls.values())
        ):
            raise ResourceConflictError("Browser control set exceeds its boundary")
        calls = 0

        async def generate(params: LLMGenerateInput) -> LLMStructuredGenerateResult:
            nonlocal calls
            calls += 1
            if calls > 4 or not isinstance(params, LLMStructuredGenerateParams):
                raise ResourceConflictError("Browser inference exceeded its step budget")
            messages = []
            for message in params.messages:
                content = (
                    message.content if isinstance(message.content, list) else [message.content]
                )
                texts = []
                for item in content:
                    if not isinstance(item.root, LLMTextContent):
                        raise ResourceConflictError("Browser inference accepts sanitized text only")
                    texts.append(item.root.text)
                messages.append(
                    {
                        "role": message.role.value,
                        "text": "\n".join(texts),
                    }
                )
            if params.response_format.schema_ is None:
                raise ResourceConflictError("Browser inference requires a response schema")
            request = {
                "system": params.system_prompt or "",
                "messages": messages,
                "schema": params.response_format.schema_.model_dump(mode="json"),
            }
            if len(json.dumps(request).encode()) > 60000:
                raise ResourceConflictError("Browser inference input is too large")
            result = await self.agent.navigate(org, request)
            return LLMStructuredGenerateResult.model_validate(
                {
                    "role": "assistant",
                    "content": {"type": "text", "text": json.dumps(result)},
                    "output_format": "json_schema",
                    "structured_content": result,
                }
            )

        async with asyncio.timeout(120):
            executable = self.executable
            if executable is None:
                async with async_playwright() as engine:
                    executable = engine.chromium.executable_path
            browser = await local_browser.launch(
                executable_path=executable,
                headless=True,
                chromium_sandbox=False,
                accept_downloads=False,
            )
            stage = None
            try:
                stage = await Stagehand.create(
                    browser=browser, model=generate, cache=False, logging={"level": "off"}
                )
                page = await browser.context.new_page()
                markup = "<h1>Approved controls</h1>" + "".join(
                    f'<button id="{escape(key, quote=True)}">'
                    f"{escape(label)} [control: {escape(key)}]</button>"
                    for key, label in controls.items()
                )
                await page.evaluate("document.body.innerHTML = " + json.dumps(markup))
                result = await stage.extract(
                    objective
                    + " Choose the matching control from "
                    + json.dumps(controls)
                    + ". Return its exact control ID, without a selector prefix, or stop. "
                    + "Include a brief public decision summary in one sentence, based only on "
                    + "these controls. Do not invent unseen page or verification details.",
                    Choice,
                    page=page,
                    screenshot=False,
                    timeout=45000,
                    cache=False,
                )
                if result.data.control not in controls:
                    raise ResourceConflictError("Agent did not select an approved browser control")
                return result.data
            finally:
                try:
                    if stage:
                        await stage.close()
                finally:
                    await browser.close()
