from collections.abc import Awaitable, Callable
from typing import Any

from connectors.base.errors import ConnectorError
from contracts import AgentKind, AgentResult, AgentTask, PlaybookDraft, WalkthroughAnalysis
from core.errors import PlaybookError, ResourceConflictError
from core.private import PrivateClient

from agents.decision import Decision, DecisionInput


class NativeAgent:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

    async def build(self, task: AgentTask) -> AgentResult:
        if task.agent is not AgentKind.PLAYBOOK or task.skill != "build_playbook":
            raise PlaybookError("Choose a playbook generation task")
        sources = [
            WalkthroughAnalysis.model_validate(value)
            for value in task.context.get("walkthroughs", ())
        ]
        result = await self.client.call(
            "/_internal/playbooks",
            {
                "organisation_id": task.organisation_id,
                "input": {
                    "id": task.id,
                    "objective": task.objective,
                    "sources": [
                        {
                            "id": source.source_id,
                            "text": "\n".join(
                                item.text for item in (*source.transcript, *source.screen_text)
                            ),
                        }
                        for source in sources
                    ],
                    "schema": PlaybookDraft.model_json_schema(),
                },
            },
            timeout=150,
        )
        return AgentResult.model_validate(
            {
                "task_id": task.id,
                "agent": task.agent,
                "skill": task.skill,
                "evidence_ids": task.evidence_ids,
            }
            | result
        )

    async def navigate(
        self,
        organisation_id: str,
        value: dict[str, Any],
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        previous = ""
        async for event in self.client.stream(
            "/_internal/navigation", {"organisation_id": organisation_id, "input": value}
        ):
            if event.get("type") == "summary":
                content = event.get("content")
                if (
                    not isinstance(content, str)
                    or len(content) > 4096
                    or not content.startswith(previous)
                ):
                    raise ResourceConflictError("Browser explanation changed while streaming")
                if progress:
                    await progress(content)
                previous = content
            elif event.get("type") == "result" and isinstance(event.get("result"), dict):
                result: dict[str, Any] = event["result"]
                if previous and result.get("summary") != previous:
                    raise ResourceConflictError("Browser explanation did not match its result")
                return result
            elif event.get("type") == "error" and event.get("code") == "ai-daily-limit":
                raise ConnectorError(
                    "ai-daily-limit", "Kinu reached this workspace's daily AI limit"
                )
            else:
                raise ResourceConflictError(
                    "Browser inference failed; check the Builder connection"
                )
        raise ResourceConflictError("Browser inference was incomplete")

    async def assess(self, organisation_id: str, value: DecisionInput) -> Decision:
        return Decision.model_validate(
            await self.client.call(
                "/_internal/assessment",
                {
                    "organisation_id": organisation_id,
                    "input": value.model_dump(mode="json"),
                },
            )
        )
