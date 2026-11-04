import assert from "node:assert/strict"
import test from "node:test"
import { rotationTrigger } from "../src/lib/format.ts"
import { currentRotation, currentRotations, isActionableApproval, isBlockingRotation } from "../src/lib/rotation-state.ts"

const credential = { id: "key_one", organisation_id: "org_one", control_version: "controls_one" }
const run = (id, status, day, changes = {}) => ({
  id, status, organisation_id: "org_one", credential_id: "key_one", control_version: "controls_one",
  created_at: `2026-10-${String(day).padStart(2, "0")}T00:00:00Z`,
  plan_hash: "plan_one", current_generation_id: "generation_one", target_generation_id: "generation_two",
  ...changes,
})

test("current key status does not keep old failed attempts after a later success", () => {
  const failed = run("failed", "failed", 1, { updated_at: "2026-11-01T00:00:00Z" })
  const completed = run("completed", "completed", 2)
  assert.deepEqual(currentRotations([failed, completed]), [completed])
  assert.equal(currentRotation([completed, failed], credential), completed)
  assert.equal(failed.status, "failed")
})

test("repeated failures group by key and retain the latest failure", () => {
  const latest = run("latest", "failed", 3)
  assert.deepEqual(currentRotations([run("old", "failed", 1), latest, run("middle", "failed", 2)]), [latest])
})

test("older cleanup and active work cannot disappear behind a newer terminal run", () => {
  for (const status of ["cleanup-required", "recovering", "running", "paused", "pending"]) {
    const blocking = run("blocking", status, 1)
    assert.equal(isBlockingRotation(blocking), true)
    assert.equal(currentRotation([run("newer", "completed", 2), blocking], credential), blocking)
  }
  assert.equal(isBlockingRotation(run("failed", "failed", 1)), false)
})

test("current key status is scoped by workspace and credential identity", () => {
  const own = run("own", "completed", 1)
  const foreign = run("foreign", "cleanup-required", 2, { organisation_id: "org_two" })
  const otherKey = run("other-key", "failed", 3, { credential_id: "key_two" })
  assert.equal(currentRotation([foreign, otherKey, own], credential), own)
  assert.equal(currentRotations([foreign, otherKey, own]).length, 3)
})

test("approval actions require a live matching run, generation, controls, and unexpired request", () => {
  const now = Date.parse("2026-11-01T00:00:00Z")
  const active = run("active", "paused", 1)
  const approval = { id: "approval", run_id: "active", organisation_id: "org_one", decision: "pending", plan_hash: "plan_one", generation_id: "generation_one", expires_at: "2026-11-02T00:00:00Z", consumed_at: null }
  assert.equal(isActionableApproval(approval, active, credential, now), true)
  for (const status of ["completed", "failed", "cancelled", "compensated", "cleanup-required"]) {
    assert.equal(isActionableApproval(approval, { ...active, status }, credential, now), false)
  }
  for (const change of [{ plan_hash: "old_plan" }, { generation_id: "old_generation" }, { organisation_id: "other_org" }, { expires_at: "2026-10-31T00:00:00Z" }, { consumed_at: "2026-10-31T00:00:00Z" }]) {
    assert.equal(isActionableApproval({ ...approval, ...change }, active, credential, now), false)
  }
  assert.equal(isActionableApproval(approval, active, { ...credential, control_version: "changed" }, now), false)
  assert.equal(isActionableApproval(approval, active, { ...credential, archived_at: "2026-10-31" }, now), false)
  assert.equal(isActionableApproval(approval, undefined, credential, now), false)
})

test("manual rotations keep the same trigger regardless of the requesting interface", () => {
  for (const source of ["agent-native", "dashboard", "console", "manual"]) {
    assert.equal(rotationTrigger({ source, kind: "manual" }), "Manual rotation")
  }
})

test("rotation triggers describe the event rather than its transport", () => {
  assert.equal(rotationTrigger({ source: "gmail", kind: "credential-exposure-detected" }), "Verified exposure")
  assert.equal(rotationTrigger({ source: "agent-native", kind: "credential-provider-drift" }), "Configuration drift")
  assert.equal(rotationTrigger({ source: "detection", kind: "credential-expiring" }), "Scheduled rotation")
  assert.equal(rotationTrigger({ source: "scheduler", kind: "credential-rotation-due" }), "Scheduled rotation")
})
