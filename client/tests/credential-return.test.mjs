import assert from "node:assert/strict"
import { test } from "node:test"
import { authorizeConnection, connectionCallbackIntegration } from "../src/lib/callback.ts"
import { credentialReturn, finishCredentialConnection, saveCredentialReturn } from "../src/lib/credential-return.ts"
import { dashboardPath, dashboardRoute } from "../src/lib/navigation.ts"

function browser(t) {
  const entries = new Map()
  const location = new URL("https://kinu.test/dashboard/credentials?add=1")
  location.assign = (url) => { location.href = url }
  const history = { replaceState(_state, _unused, url) { location.href = new URL(url, location).href } }
  const storage = { setItem: (key, value) => entries.set(key, value), getItem: (key) => entries.get(key) ?? null, removeItem: (key) => entries.delete(key) }
  for (const [name, value] of Object.entries({ window: { location, history }, sessionStorage: storage })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { value, configurable: true })
    t.after(() => previous ? Object.defineProperty(globalThis, name, previous) : delete globalThis[name])
  }
  return location
}

test("a credential connection returns to its original workspace and step only for the matching sign-in", (t) => {
  const location = browser(t)
  const draft = { source: "hosting", hostId: "host_render", form: { project: "mail", variable: "RESEND_API_KEY", provider: "issuer", step: 1 } }
  authorizeConnection("https://mail.example/authorize", "gmail", (integration) => saveCredentialReturn("org_one", integration, draft))
  location.href = "https://kinu.test/dashboard/connections?gmail=mailbox&workspace=org_one"
  assert.deepEqual(credentialReturn("org_one", connectionCallbackIntegration()), draft)
  assert.equal(credentialReturn("org_two", "gmail"), null)
  assert.equal(credentialReturn("org_one", "railway"), null)
  location.search = "?gmail=mailbox&workspace=org_two"
  assert.equal(credentialReturn("org_one", "gmail"), null)
  finishCredentialConnection()
  assert.equal(dashboardPath(dashboardRoute()), "/dashboard/credentials?add=1")
  assert.equal(credentialReturn("org_one", "gmail"), null)
})

test("a standalone sign-in and an expired credential flow cannot reopen an old draft", (t) => {
  const location = browser(t)
  const draft = { source: "hosting", hostId: "" }
  saveCredentialReturn("org_one", "railway", draft)
  const start = Date.now()
  t.mock.method(Date, "now", () => start + 61 * 60_000)
  assert.equal(credentialReturn("org_one", "railway"), null)
  saveCredentialReturn("org_one", "railway", draft)
  authorizeConnection("https://railway.example/authorize", "railway")
  location.href = "https://kinu.test/dashboard/connections?hosting=connection_first&platform=railway"
  assert.equal(credentialReturn("org_one", connectionCallbackIntegration()), null)
  assert.deepEqual(dashboardRoute(), { section: "connections", resourceId: undefined })
})

test("playbook details and settings tabs round-trip through the URL", (t) => {
  const location = browser(t)
  location.href = "https://kinu.test/dashboard/playbooks/playbook_one"
  assert.deepEqual(dashboardRoute(), { section: "playbooks", resourceId: "playbook_one" })
  assert.equal(dashboardPath({ section: "playbooks", resourceId: "playbook_one" }), "/dashboard/playbooks/playbook_one")
  location.href = "https://kinu.test/dashboard/settings?tab=team"
  assert.deepEqual(dashboardRoute(), { section: "settings", tab: "team" })
  assert.equal(dashboardPath({ section: "settings", tab: "team" }), "/dashboard/settings?tab=team")
  assert.equal(dashboardPath({ section: "settings", tab: "ai" }), "/dashboard/settings?tab=ai")
  assert.equal(dashboardPath({ section: "settings" }), "/dashboard/settings")
  location.href = "https://kinu.test/dashboard/settings?tab=nope"
  assert.deepEqual(dashboardRoute(), { section: "settings", resourceId: undefined })
})
