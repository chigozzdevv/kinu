import assert from "node:assert/strict"
import test from "node:test"
import { approvedProjects, variableLabel } from "../src/lib/hosting.ts"

test("a Render variable only gets an environment label when the provider supplies a name", () => {
  const variable = { name: "RESEND_API_KEY", environment: "production", environment_name: null, service_name: null }
  assert.equal(variableLabel(variable), "RESEND_API_KEY")
  assert.equal(variableLabel({ ...variable, environment_name: "Staging" }), "RESEND_API_KEY · Staging")
})

test("approved runtimes keep their known projects available when discovery is offline", () => {
  const connection = { platform: "render", allowed_resources: ["render://svc-a", "render://svc%2Fb", "render://%"] }
  const mapped = [{ project: { id: "svc-a", name: "kinu-check" } }, { project: { id: "removed", name: "Former app" } }]
  assert.deepEqual(approvedProjects(connection, mapped), [{ id: "svc-a", name: "kinu-check" }, { id: "svc/b", name: undefined }])
})
