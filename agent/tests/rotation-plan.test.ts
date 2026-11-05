import assert from "node:assert/strict"
import test from "node:test"
import { createBuilderEngine, type AgentEngine } from "@agent-native/core/agent/engine"
import { planInput, planStages, propose } from "../server/rotation-plan"
import { internalResponse } from "../server/internal"

const input = { trigger: "exposure" as const, method: "browser" as const, app_count: 1, approval_required: true, observation_seconds: 60 }
function engine(value: unknown, complete = true): AgentEngine {
  return { ...createBuilderEngine(), async *stream(options) {
    assert.deepEqual(options.tools, [])
    assert.equal(JSON.stringify(options.messages).includes("secret_reference"), false)
    yield { type: "text-delta", text: JSON.stringify(value) }
    if (complete) yield { type: "stop", reason: "end_turn" }
  } }
}
const good = { strategy: "parallel", stages: [...planStages], rationale: "Keep the old key until verification and approval." }

test("rotation planning receives only bounded facts and preserves the model response", async () => {
  assert.equal(planInput.safeParse({ ...input, secret: "not allowed" }).success, false)
  assert.deepEqual(await propose(input, engine(good), "test"), good)
  await assert.rejects(propose(input, engine(good, false), "test"), /incomplete/)
})

test("rotation planning cannot remove, reorder or change required controls", async () => {
  await assert.rejects(propose(input, engine({ ...good, stages: good.stages.filter((s) => s !== "approval") }), "test"))
  await assert.rejects(propose(input, engine({ ...good, stages: [...good.stages].reverse() }), "test"), /required rotation stages/)
  await assert.rejects(propose(input, engine({ ...good, strategy: "immediate" }), "test"))
})

test("rotation planner endpoint requires the private token and rejects extra input", async () => {
  const before = process.env.KINU_NATIVE_TOKEN
  process.env.KINU_NATIVE_TOKEN = "test-internal-token"
  try {
    const request = (token: string, body: unknown) => new Request("http://localhost/_internal/rotation-plan", {
      method: "POST", headers: { authorization: token, "content-type": "application/json" }, body: JSON.stringify(body),
    })
    assert.equal((await internalResponse(request("Bearer wrong", { organisation_id: "org_one", input }))).status, 401)
    assert.equal((await internalResponse(request("Bearer test-internal-token", { organisation_id: "org_one", input: { ...input, secret: "forbidden" } }))).status, 422)
  } finally {
    if (before === undefined) delete process.env.KINU_NATIVE_TOKEN
    else process.env.KINU_NATIVE_TOKEN = before
  }
})
