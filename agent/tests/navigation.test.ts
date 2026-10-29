import assert from "node:assert/strict"
import test from "node:test"
import { createBuilderEngine, type AgentEngine } from "@agent-native/core/agent/engine"
import { infer, navigationSchema } from "../server/navigation"

const input = { system: "Choose the approved control", messages: [{ role: "user" as const, text: "Create key" }], schema: { type: "object", properties: { control: { type: "string" } } } }

function engine(text: string, complete = true): AgentEngine {
  return { ...createBuilderEngine(), async *stream(options) {
    assert.deepEqual(options.tools, [])
    assert.equal(options.maxOutputTokens, 2048)
    yield { type: "text-delta", text }
    if (complete) yield { type: "stop", reason: "end_turn" }
  } }
}

test("navigation accepts sanitized text with a schema and bounded structured output", async () => {
  assert.equal(navigationSchema.safeParse({ ...input, cookies: "private" }).success, false)
  assert.equal(navigationSchema.safeParse({ ...input, messages: [{ role: "user", image: "private" }] }).success, false)
  assert.deepEqual(await infer(input, engine('{"control":"create"}'), "test"), { control: "create" })
  await assert.rejects(infer(input, engine('{"control":"create"}', false), "test"), /incomplete/)
  await assert.rejects(infer(input, engine("x".repeat(16385)), "test"), /boundary/)
  await assert.rejects(infer(input, engine("[]"), "test"), /invalid JSON/)
})

test("navigation streams only the public explanation before the result completes", async () => {
  const summary = 'I will create the scoped key for the replacement. Keep the "sending" permission.\n' .repeat(5) + "Ready ✅"
  const wire = JSON.stringify({ control: "create_key", summary })
  let completed = false
  const seen: string[] = [], early: boolean[] = []
  const streaming: AgentEngine = { ...createBuilderEngine(), async *stream() {
    for (let offset = 0; offset < wire.length; offset += 7) yield { type: "text-delta", text: wire.slice(offset, offset + 7) }
    completed = true
    yield { type: "stop", reason: "end_turn" }
  } }
  const result = await infer(input, streaming, "test", (text) => {
    seen.push(text)
    early.push(!completed)
  })
  assert.deepEqual(result, { control: "create_key", summary })
  assert.ok(seen.length >= 2 && early[0])
  assert.equal(seen.at(-1), summary)
  assert.ok(seen.every((text) => summary.startsWith(text) && !text.includes("create_key")))
})
