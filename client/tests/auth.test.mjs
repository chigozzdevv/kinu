import assert from "node:assert/strict"
import { test } from "node:test"

let moduleId = 0

async function browser(t, pathname = "/dashboard/settings", search = "?tab=ai") {
  const stored = new Map()
  const redirects = []
  const timers = []
  const cleared = []
  const window = Object.assign(new EventTarget(), {
    location: { pathname, search, replace: (url) => redirects.push(url) },
    setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length },
    clearTimeout: (id) => cleared.push(id),
  })
  const values = {
    window,
    document: new EventTarget(),
    sessionStorage: {
      getItem: (key) => stored.get(key) ?? null,
      setItem: (key, value) => stored.set(key, value),
      removeItem: (key) => stored.delete(key),
    },
  }
  for (const [key, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { value, configurable: true })
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key])
  }
  const auth = await import(`../src/lib/auth.ts?test=${++moduleId}`)
  return { auth, stored, redirects, timers, cleared, window, document: values.document }
}

function session(expiresAt) {
  return { identity: { uid: "one", email: "owner@example.com", emailVerified: true, displayName: "Owner" },
    csrf_token: "private-csrf", expires_at: new Date(expiresAt).toISOString() }
}

test("API and AI expiry redirect once and preserve the exact dashboard destination", async (t) => {
  const { auth, stored, redirects, window } = await browser(t)
  let events = 0
  window.addEventListener(auth.sessionExpiredEvent, () => events++)
  t.mock.method(globalThis, "fetch", async () => Response.json({ error: "Expired" }, { status: 401 }))
  const results = await Promise.allSettled([
    auth.sessionFetch("/v1/session"), auth.sessionFetch("/_agent-native/kinu/builder/status"),
  ])
  assert.ok(results.every((result) => result.status === "rejected" && result.reason instanceof auth.SessionExpiredError))
  assert.deepEqual(redirects, ["/auth?expired=1"])
  assert.equal(events, 1)
  assert.equal(stored.get(auth.dashboardReturnKey), "/dashboard/settings?tab=ai")
})

test("permission and service failures remain API failures without signing the user out", async (t) => {
  const { auth, redirects } = await browser(t)
  for (const status of [403, 429, 503]) {
    t.mock.method(globalThis, "fetch", async () => Response.json({ message: "Unavailable" }, { status }))
    assert.equal((await auth.sessionFetch("/v1/session")).status, status)
  }
  assert.deepEqual(redirects, [])
})

test("an expired cached session prevents a write before it reaches the API", async (t) => {
  const { auth, redirects } = await browser(t)
  let now = Date.now()
  t.mock.method(Date, "now", () => now)
  const request = t.mock.method(globalThis, "fetch", async () => Response.json(session(now + 1000)))
  await auth.readSession()
  now += 1001
  await assert.rejects(auth.sessionFetch("/v1/rotation", { method: "POST", body: "{}" }), auth.SessionExpiredError)
  assert.equal(request.mock.callCount(), 1)
  assert.deepEqual(redirects, ["/auth?expired=1"])
})

test("valid writes keep cookies, CSRF and caller headers", async (t) => {
  const { auth, redirects } = await browser(t)
  const request = t.mock.method(globalThis, "fetch", async (url, options) => {
    if (url === "/auth/session") return Response.json(session(Date.now() + 3600000))
    assert.equal(options.credentials, "same-origin")
    assert.equal(options.headers.get("x-csrf-token"), "private-csrf")
    assert.equal(options.headers.get("x-kinu-workspace"), "org_one")
    assert.equal(options.headers.get("content-type"), "application/json")
    return Response.json({ ok: true })
  })
  const response = await auth.sessionFetch("/v1/rotation", {
    method: "POST", headers: { "x-kinu-workspace": "org_one" }, body: "{}",
  })
  assert.equal(response.status, 200)
  assert.equal(request.mock.callCount(), 2)
  assert.deepEqual(redirects, [])
})

test("the expiry timer redirects an idle page and is removed on cleanup", async (t) => {
  const { auth, timers, cleared, redirects } = await browser(t)
  let now = Date.now()
  t.mock.method(Date, "now", () => now)
  const cleanup = auth.watchSession(new Date(now + 3000).toISOString())
  assert.equal(timers[0].delay, 3000)
  now += 3000
  timers[0].callback()
  assert.deepEqual(redirects, ["/auth?expired=1"])
  cleanup()
  assert.deepEqual(cleared, [1])
})

test("returning to a suspended tab catches expiry even when its timer did not run", async (t) => {
  const { auth, redirects, window, document } = await browser(t)
  let now = Date.now()
  t.mock.method(Date, "now", () => now)
  auth.watchSession(new Date(now + 3000).toISOString())
  window.dispatchEvent(new Event("focus"))
  assert.deepEqual(redirects, [])
  now += 4000
  document.dispatchEvent(new Event("visibilitychange"))
  window.dispatchEvent(new Event("focus"))
  assert.deepEqual(redirects, ["/auth?expired=1"])
})

test("an unmounted session watcher no longer redirects", async (t) => {
  const { auth, redirects, window, document } = await browser(t)
  let now = Date.now()
  t.mock.method(Date, "now", () => now)
  auth.watchSession(new Date(now + 3000).toISOString())()
  now += 4000
  window.dispatchEvent(new Event("focus"))
  document.dispatchEvent(new Event("visibilitychange"))
  assert.deepEqual(redirects, [])
})

test("incorrect sign-in credentials remain an inline form error", async (t) => {
  const { auth, redirects } = await browser(t, "/auth", "")
  t.mock.method(globalThis, "fetch", async (url) => url === "/auth/form"
    ? Response.json({ csrf_token: "form-csrf" })
    : Response.json({ message: "Email or password is incorrect." }, { status: 401 }))
  await assert.rejects(auth.submitAuth("signin", { email: "owner@example.com", password: "incorrect" }), /Email or password is incorrect/)
  assert.deepEqual(redirects, [])
})
