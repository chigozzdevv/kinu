import assert from "node:assert/strict"
import test from "node:test"
import { rotationTrigger } from "../src/lib/format.ts"

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
