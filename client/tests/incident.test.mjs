import assert from "node:assert/strict"
import test from "node:test"
import { affectedResource, currentIncident } from "../src/lib/incident.ts"

const incident = { organisation_id: "org_one", credential_id: "key_one", resource: {} }
const graph = {
  credentials: [{ id: "key_one", organisation_id: "org_one", display_name: "Sandbox key", provider: "resend" }],
  bindings: [{ credential_id: "key_one", organisation_id: "org_one", service_id: "service_one" }],
  services: [{ id: "service_one", organisation_id: "org_one", display_name: "kinu-check", runtime_resource: "render://test/production" }],
}

test("credential status keeps an older actionable incident ahead of newer activity", () => {
  const oldOpen = { ...incident, id: "old", status: "action-required" }
  const rotating = { ...incident, id: "new", status: "rotation-started" }
  const resolved = { ...incident, id: "resolved", status: "resolved" }
  assert.equal(currentIncident([resolved, rotating, oldOpen], graph.credentials[0]), oldOpen)
  assert.equal(currentIncident([resolved], graph.credentials[0]), undefined)
  assert.equal(currentIncident([{ ...oldOpen, organisation_id: "another_workspace" }], graph.credentials[0]), undefined)
})

test("confirmed credentials show their mapped service when email fields are empty", () => {
  assert.deepEqual(affectedResource(incident, graph), { name: "kinu-check", detail: "Render" })
  assert.deepEqual(incident.resource, {})
})

test("explicit source resources take priority and unconfirmed candidates do not become facts", () => {
  assert.deepEqual(affectedResource({ ...incident, resource: { repository: "org/repo", environment: "prod" } }, graph), { name: "org/repo", detail: "prod" })
  assert.equal(affectedResource({ ...incident, credential_id: null, candidates: [{ credential_id: "key_one" }] }, graph).name, "Not identified")
})

test("resource fallback cannot cross workspaces or unrelated credential bindings", () => {
  assert.equal(affectedResource({ ...incident, organisation_id: "org_other" }, graph).name, "Not identified")
  assert.equal(affectedResource(incident, { ...graph, bindings: [{ ...graph.bindings[0], credential_id: "another_key" }] }).name, "Sandbox key")
})
