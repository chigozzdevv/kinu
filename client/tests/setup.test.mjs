import assert from "node:assert/strict"
import { test } from "node:test"
import { captureSetupLink, claimInstallation, setupKey } from "../src/lib/setup.ts"

test("installation setup removes the private link from the URL and claims only with an authenticated CSRF request", async (t) => {
  const stored = new Map()
  const replacements = []
  const token = "a".repeat(96)
  const window = {
    location: { pathname: "/auth", search: "", hash: "#setup=invalid" },
    history: { replaceState: (_state, _unused, url) => replacements.push(url) },
  }
  for (const [key, value] of Object.entries({ window, sessionStorage: {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
    removeItem: (key) => stored.delete(key),
  } })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { value, configurable: true })
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key])
  }
  captureSetupLink()
  assert.equal(stored.size, 0)
  window.location.hash = "#setup=" + token
  captureSetupLink()
  assert.equal(stored.get(setupKey), token)
  assert.deepEqual(replacements, ["/auth"])
  let accepted = false
  const calls = []
  t.mock.method(globalThis, "fetch", async (path, options) => {
    calls.push(path)
    if (path === "/auth/session") return Response.json({ csrf_token: "setup-csrf", expires_at: new Date(Date.now() + 60_000).toISOString() })
    assert.equal(path, "/v1/installation/claim")
    assert.equal(options.method, "POST")
    assert.equal(options.credentials, "same-origin")
    assert.equal(options.headers.get("x-csrf-token"), "setup-csrf")
    assert.deepEqual(JSON.parse(options.body), { token })
    return Response.json(accepted ? { administrator: true } : { message: "Setup unavailable" }, { status: accepted ? 200 : 409 })
  })
  await assert.rejects(claimInstallation(), /Setup unavailable/)
  assert.equal(stored.get(setupKey), token)
  accepted = true
  await claimInstallation()
  assert.equal(stored.has(setupKey), false)
  const count = calls.length
  await claimInstallation()
  assert.equal(calls.length, count)
})
