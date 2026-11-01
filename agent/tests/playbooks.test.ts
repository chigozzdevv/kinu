import assert from "node:assert/strict"
import test from "node:test"
import { createBuilderEngine, type AgentEngine } from "@agent-native/core/agent/engine"
import { draft, playbookSchema } from "../server/playbooks"

const input = { id: "task_one", objective: "Build a browser procedure", sources: [{ id: "source_one", text: "Open Keys and create a scoped replacement." }], schema: { type: "object" } }
function engine(text: string, stop: "end_turn" | "max_tokens" = "end_turn", complete = true): AgentEngine {
  return { ...createBuilderEngine(), async *stream(options) {
    assert.deepEqual(options.tools, [])
    assert.equal(options.maxOutputTokens, 12288)
    assert.match(options.systemPrompt, /replacement_provider_display_name/)
    yield { type: "text-delta", text }
    if (complete) yield { type: "stop", reason: stop }
  } }
}

test("playbook generation uses bounded structured text without tools or video", async () => {
  const result = { playbook_draft: { name: "Issuer rotation", platform: "issuer" } }
  assert.deepEqual(await draft(input, engine('```json\n' + JSON.stringify(result) + '\n```'), "test"), result)
  assert.equal(playbookSchema.safeParse({ ...input, video: "private.mp4" }).success, false)
  assert.equal(playbookSchema.safeParse({ ...input, sources: [{ id: "source_one", file: "private" }] }).success, false)
  assert.deepEqual(await draft(input, engine('{"error":"Add the issuer identity endpoint."}'), "test"), { error: "Add the issuer identity endpoint." })
})

test("unfinished or oversized model output cannot become a draft", async () => {
  await assert.rejects(draft(input, engine('{"playbook_draft":{}}', "end_turn", false), "test"), /incomplete/)
  await assert.rejects(draft(input, engine('{"playbook_draft":{}}', "max_tokens"), "test"), /complete/)
  await assert.rejects(draft(input, engine('not JSON'), "test"))
  await assert.rejects(draft(input, engine('x'.repeat(128001)), "test"), /too large/)
  const tools: AgentEngine = { ...createBuilderEngine(), async *stream() {
    yield { type: "tool-call", id: "action_one", name: "browser.click", input: {} }
  } }
  await assert.rejects(draft(input, tools, "test"), /cannot execute tools/)
})
