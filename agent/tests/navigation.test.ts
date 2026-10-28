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
