import assert from "node:assert/strict"
import test from "node:test"
import { assess, inputSchema } from "../server/decision"
import { createBuilderEngine, type AgentEngine } from "@agent-native/core/agent/engine"

const incident = { stage: "investigate" as const, kind: "credential-exposure-detected" as const, confidence: "verified" as const, candidate_count: 1, policy_allows_rotation: true }
function engine(result: unknown, complete = true): AgentEngine {
  return { ...createBuilderEngine(), async *stream(options) {
    assert.deepEqual(options.tools, [])
    assert.equal(options.maxOutputTokens, 192)
    assert.equal(JSON.stringify(options).includes("secret_reference"), false)
    yield { type: "text-delta", text: JSON.stringify(result) }
    if (complete) yield { type: "stop", reason: "end_turn" }
  } }
}

test("assessment only accepts bounded evidence and a completed model response", async () => {
  assert.equal(inputSchema.safeParse({ ...incident, secret: "unsafe" }).success, false)
  assert.equal(inputSchema.safeParse({ ...incident, candidate_count: 501 }).success, false)
  await assert.rejects(assess(incident, engine({ decision: "rotate", reason: "confirmed-exposure" }, false), "test"), /incomplete/)
  assert.deepEqual(await assess(incident, engine({ decision: "rotate", reason: "confirmed-exposure" }), "test"), { decision: "rotate", reason: "confirmed-exposure" })
})

test("model cannot override policy, ambiguity, or confidence", async () => {
  for (const input of [{ ...incident, policy_allows_rotation: false }, { ...incident, candidate_count: 2 }, { ...incident, confidence: "medium" as const }]) {
    await assert.rejects(assess(input, engine({ decision: "rotate", reason: "confirmed-exposure" }), "test"), /rotation policy/)
  }
  await assert.rejects(assess(incident, engine({ decision: "ignore", reason: "unrelated" }), "test"), /evidence/)
  await assert.rejects(assess(incident, engine({ decision: "click-revoke", reason: "approved-control-visible" }), "test"), /rotation policy/)
})

test("browser phase rejects non-PNG input and the wrong mutation", async () => {
  const browser = { stage: "browser" as const, phase: "create" as const, redacted: true as const, image: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0]).toString("base64") }
  await assert.rejects(assess({ ...browser, image: "thisisnotapngfile" }, engine({}), "test"), /PNG/)
  await assert.rejects(assess(browser, engine({ decision: "click-revoke", reason: "approved-control-visible" }), "test"), /browser phase/)
})
