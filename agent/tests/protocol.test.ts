import assert from "node:assert/strict"
import { test } from "node:test"
import { decodeAgUiEvent, encodeAgentEvent, type AgentEvent } from "@agent-native/agentkit/protocol"

test("AgentKit events survive protocol serialization", () => {
  const context = { threadId: "thread_one", runId: "run_one" }
  const envelope = { ...context, id: "event_one", sequence: 1, occurredAt: "2026-10-26T12:00:00Z" }
  const events: AgentEvent[] = [
    { ...envelope, type: "run.started" },
    { ...envelope, type: "run.completed" },
    { ...envelope, type: "message.delta", messageId: "message_one", text: "Ready" },
    { ...envelope, type: "reasoning.delta", messageId: "message_one", text: "Checking" },
    { ...envelope, type: "run.failed", error: { code: "unavailable", message: "Unavailable" } },
  ]
  for (const event of events) {
    assert.deepEqual(decodeAgUiEvent(encodeAgentEvent(event), context), event)
  }
})
