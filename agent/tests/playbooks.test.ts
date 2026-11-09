import assert from "node:assert/strict"
import test from "node:test"
import { createBuilderEngine, type AgentEngine } from "@agent-native/core/agent/engine"
import { draft, playbookSchema } from "../server/playbooks"

const input = { id: "task_one", objective: "Build a browser procedure", sources: [{ id: "source_one", text: "Open Keys and create a scoped replacement." }], schema: { type: "object" } }
function engine(text: string, stop: "end_turn" | "max_tokens" = "end_turn", complete = true): AgentEngine {
  return { ...createBuilderEngine(), async *stream(options) {
    assert.deepEqual(options.tools, [])
    assert.equal(options.maxOutputTokens, 16384)
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
  await assert.rejects(draft(input, engine('{"playbook_draft":{}}', "max_tokens"), "test"), /whole output budget/)
  await assert.rejects(draft(input, engine('not JSON'), "test"))
  await assert.rejects(draft(input, engine('x'.repeat(128001)), "test"), /too large/)
  const tools: AgentEngine = { ...createBuilderEngine(), async *stream() {
    yield { type: "tool-call", id: "action_one", name: "browser.click", input: {} }
  } }
  await assert.rejects(draft(input, tools, "test"), /cannot execute tools/)
})

test("API playbooks use provider operations without browser instructions", async () => {
  const apiEngine: AgentEngine = { ...createBuilderEngine(), async *stream(options) {
    assert.deepEqual(options.tools, [])
    assert.match(options.systemPrompt, /read-only identity check/)
    assert.match(options.systemPrompt, /\{provider_id\}/)
    assert.doesNotMatch(options.systemPrompt, /browser\.secure-capture|deterministic selector/)
    yield { type: "text-delta", text: '{"error":"Add the provider identity endpoint."}' }
    yield { type: "stop", reason: "end_turn" }
  } }
  assert.deepEqual(await draft({ ...input, execution: "api" }, apiEngine, "test"), { error: "Add the provider identity endpoint." })
  assert.equal(playbookSchema.safeParse({ ...input, execution: "shell" }).success, false)
})

test("browser playbooks ground selectors in sources and leave the verifier optional", async () => {
  let browserPrompt = ""
  const capture: AgentEngine = { ...createBuilderEngine(), async *stream(options) {
    browserPrompt = options.systemPrompt
    yield { type: "text-delta", text: '{"error":"Add the exact dialog controls."}' }
    yield { type: "stop", reason: "end_turn" }
  } }
  await draft({ ...input, objective: 'Build a browser credential-rotation procedure for the exact platform "resend".' }, capture, "test")
  assert.match(browserPrompt, /only when that exact label appears/)
  assert.match(browserPrompt, /\[role="dialog"\] button\[type="submit"\]/)
  assert.match(browserPrompt, /\$\{old_provider_id\}/)
  assert.match(browserPrompt, /verifier is optional/)
  assert.doesNotMatch(browserPrompt, /resend-receipt/)

  let genericPrompt = ""
  const generic: AgentEngine = { ...createBuilderEngine(), async *stream(options) {
    genericPrompt = options.systemPrompt
    yield { type: "text-delta", text: '{"error":"Add the issuer identity endpoint."}' }
    yield { type: "stop", reason: "end_turn" }
  } }
  await draft({ ...input, objective: "Build a browser procedure" }, generic, "test")
  assert.doesNotMatch(genericPrompt, /resend-receipt/)
  assert.match(genericPrompt, /verifier is optional/)
})

test("a bare draft is wrapped and an empty reasoning budget is retryable", async () => {
  const bare = { name: "Issuer", platform: "issuer", steps: [{ id: "one" }] }
  assert.deepEqual(await draft(input, engine(JSON.stringify(bare)), "test"), { playbook_draft: bare })
  await assert.rejects(draft(input, engine("", "max_tokens"), "test"), /whole output budget/)
})
