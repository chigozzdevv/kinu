import assert from "node:assert/strict"
import { test } from "node:test"
import { loadConfig } from "../server/config"
import { resolveSession, sessionCookie } from "../server/session"

const config = loadConfig({})
const token = "a".repeat(43)
const headers = new Headers({ cookie: `kinu.session=${token}`, "x-kinu-workspace": "org_one" })

function body() {
  return {
    identity: { uid: "issuer#user-one", email: "owner@example.com", emailVerified: true, displayName: "Owner" },
    workspace: { id: "org_one", role: "viewer" },
    csrf_token: "not-an-agent-credential",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  }
}

test("session resolves through the API without exposing credentials", async () => {
  const request: typeof fetch = async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8000/auth/session")
    assert.equal(new Headers(options?.headers).get("cookie"), `kinu.session=${token}`)
    assert.equal(new Headers(options?.headers).get("x-kinu-workspace"), "org_one")
    assert.equal(options?.redirect, "error")
    return Response.json(body())
  }
  const session = await resolveSession(headers, config, request)
  assert.equal(session?.userId, "issuer#user-one")
  assert.equal(session?.orgId, "org_one")
  assert.equal(session?.orgRole, "viewer")
  assert.equal(session?.token, undefined)
  assert.equal(JSON.stringify(session).includes("csrf"), false)
})

test("other browser cookies are not forwarded", () => {
  assert.equal(sessionCookie(new Headers({ cookie: `provider-key=private; kinu.session=${token}` }), config), `kinu.session=${token}`)
  assert.equal(sessionCookie(new Headers({ cookie: `kinu.session=${token}; kinu.session=${token}` }), config), null)
})

test("revoked or missing sessions are not authenticated", async () => {
  assert.equal(await resolveSession(new Headers(), config), null)
  assert.equal(await resolveSession(headers, config, async () => new Response(null, { status: 401 })), null)
})

test("bearer credentials cannot replace the Kinu cookie", async () => {
  await assert.rejects(resolveSession(new Headers({ authorization: "Bearer arbitrary" }), config), { status: 401 })
})

test("workspace identity is checked against the API response", async () => {
  await assert.rejects(resolveSession(new Headers({ cookie: `kinu.session=${token}` }), config), { status: 400 })
  await assert.rejects(resolveSession(headers, config, async () => Response.json({ ...body(), workspace: { id: "org_other", role: "administrator" } })), { status: 403 })
  await assert.rejects(resolveSession(headers, config, async () => new Response(null, { status: 403 })), { status: 403 })
})

test("expired sessions and malformed identity data are rejected", async () => {
  assert.equal(await resolveSession(headers, config, async () => Response.json({ ...body(), expires_at: "2020-01-01T00:00:00Z" })), null)
  await assert.rejects(resolveSession(headers, config, async () => Response.json({ identity: { email: "owner@example.com" } })), { status: 503 })
})

test("API failures stay unavailable without falling back to another identity", async () => {
  await assert.rejects(resolveSession(headers, config, async () => { throw new Error("offline") }), { status: 503 })
})

test("unsafe auth configuration and private API credentials are rejected", () => {
  for (const env of [
    { AUTH_DISABLED: "true" }, { AUTH_MODE: "local" }, { ACCESS_TOKEN: "static" },
    { KINU_DATABASE_URL: "private" }, { KINU_API_URL: "https://user:password@example.com" },
    { KINU_GMAIL_SECRET_ARN: "private" }, { KINU_GMAIL_TABLE: "private" },
    { KINU_ENVIRONMENT: "production", KINU_PUBLIC_URL: "http://localhost:5173" },
  ]) assert.throws(() => loadConfig(env))
})
