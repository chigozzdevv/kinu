from contracts import AgentKind, AgentResult, AgentTask
from core.errors import PlaybookError
from core.private import PrivateClient


class PlaybookBuilder:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

    async def execute(self, task: AgentTask) -> AgentResult:
        if task.agent is not AgentKind.PLAYBOOK or task.skill != "build_playbook":
            raise PlaybookError("Choose a playbook generation task")
        result = AgentResult.model_validate(
            await self.client.call("/playbooks/generate", task.model_dump(mode="json"), timeout=180)
        )
        if (result.task_id, result.agent, result.skill, result.evidence_ids) != (
            task.id,
            task.agent,
            task.skill,
            task.evidence_ids,
        ):
            raise PlaybookError("The generated result belongs to another request")
        return result
