import assert from "node:assert/strict"
import { createHash, randomBytes } from "node:crypto"
import { readFile } from "node:fs/promises"
import { PGlite } from "@electric-sql/pglite"
import { PGLiteSocketServer } from "@electric-sql/pglite-socket"
import { freePort, root, service, stop } from "./fixture"

const db = await PGlite.create()
const socket = new PGLiteSocketServer({ db, port: 0, host: "127.0.0.1", maxConnections: 5 })
const children: ReturnType<typeof service>[] = []
try {
  await db.exec(await readFile(`${root}server/core/storage/schema.sql`, "utf8"))
  await socket.start()
  const apiUrl = `http://127.0.0.1:${await freePort()}`
  const agentUrl = `http://127.0.0.1:${await freePort()}`
  const publicUrl = "http://localhost:5173"
  const identity = {
    subject: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example#user-one",
    issuer: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
    email: "owner@example.com", email_verified: true, display_name: "Owner", connected_via: "Cognito",
  }
  const token = randomBytes(32).toString("base64url")
  const digest = createHash("sha256").update(token).digest("hex")
  const csrf = randomBytes(32).toString("base64url")
  await db.query("INSERT INTO kinu.sessions VALUES ($1, $2, $3, $4)", [
    digest, JSON.stringify(identity), csrf, new Date(Date.now() + 300_000),
  ])
  const api = service("uv", ["run", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", new URL(apiUrl).port, "--no-access-log"], root, {
    ...process.env,
    UV_CACHE_DIR: ".cache/uv", UV_PYTHON_INSTALL_DIR: ".cache/python",
    KINU_ENVIRONMENT: "development", KINU_PUBLIC_URL: publicUrl,
    KINU_DATABASE_URL: `postgresql://postgres:postgres@${socket.getServerConn()}/postgres?sslmode=disable`, KINU_TELEMETRY_ENABLED: "false",
    KINU_COGNITO_POOL_ID: "us-east-1_example", KINU_COGNITO_CLIENT_ID: "kinuclient",
    KINU_COGNITO_DOMAIN: "https://login.example.com",
  })
  children.push(api)
  await api.ready(`${apiUrl}/health/ready`)
  const agentEnv = { ...process.env }
  delete agentEnv.KINU_DATABASE_URL
  const agent = service("node", [".output/server/index.mjs"], `${root}agent`, {
    ...agentEnv, KINU_ENVIRONMENT: "development", KINU_PUBLIC_URL: publicUrl, KINU_API_URL: apiUrl,
    DATABASE_URL: "pglite:memory://",
    NITRO_HOST: "127.0.0.1", NITRO_PORT: new URL(agentUrl).port,
  })
  children.push(agent)
  await agent.ready(`${agentUrl}/health/live`)
  const cookie = `kinu.session=${token}`
  const writeHeaders = { cookie, origin: publicUrl, "x-csrf-token": csrf, "content-type": "application/json" }
  const create = await fetch(`${apiUrl}/v1/organisations`, {
    method: "POST", headers: writeHeaders, body: JSON.stringify({ name: "Test workspace" }),
  })
  const membership = await create.json() as { organisation: { id: string } }
  assert.equal(create.status, 201, JSON.stringify(membership))
  const workspace = membership.organisation.id
  const sessionUrl = `${agentUrl}/_agent-native/auth/session`
  const headers = { cookie, "x-kinu-workspace": workspace }
  const response = await fetch(sessionUrl, { headers })
  const session = await response.json() as Record<string, unknown>
  assert.equal(response.status, 200, JSON.stringify(session))
  assert.equal(response.headers.get("cache-control"), "no-store")
  assert.equal(session.userId, identity.subject)
  assert.equal(session.email, identity.email)
  assert.equal(session.orgId, workspace)
  assert.equal(session.orgRole, "administrator")
  assert.equal(session.token, undefined)
  assert.equal(JSON.stringify(session).includes(csrf), false)
  assert.equal(JSON.stringify(session).includes(token), false)
  assert.equal(response.headers.getSetCookie().every((value) => value.includes("=1;")), true)
  assert.equal((await fetch(sessionUrl)).status, 401)
  assert.equal((await fetch(sessionUrl, { headers: { authorization: "Bearer arbitrary" } })).status, 401)
  assert.equal((await fetch(sessionUrl, { headers: { cookie } })).status, 400)
  assert.equal((await fetch(`${sessionUrl}?_session=arbitrary`, { headers })).status, 404)
  const deniedHeaders = { ...headers, "x-kinu-workspace": "org_other" }
  const deniedApi = await fetch(`${apiUrl}/auth/session`, { headers: deniedHeaders })
  assert.equal(deniedApi.status, 403, await deniedApi.text())
  const deniedAgent = await fetch(sessionUrl, { headers: deniedHeaders })
  assert.equal(deniedAgent.status, 403, await deniedAgent.text())
  for (const path of ["/mcp", "/_agent-native/terminal", "/_agent-native/auth/login", "/_agent-native/auth/logout"]) {
    assert.equal((await fetch(`${agentUrl}${path}`, { headers })).status, 404)
  }
  await db.query("UPDATE kinu.members SET data = jsonb_set(data, '{enabled}', 'false') WHERE organisation_id = $1", [workspace])
  assert.equal((await fetch(sessionUrl, { headers })).status, 403)
  await db.query("UPDATE kinu.members SET data = jsonb_set(data, '{enabled}', 'true') WHERE organisation_id = $1", [workspace])
  assert.equal((await fetch(sessionUrl, { headers })).status, 200)
  assert.equal((await fetch(`${apiUrl}/v1/organisations`, { method: "POST", headers: { cookie, "content-type": "application/json" }, body: JSON.stringify({ name: "Forged" }) })).status, 403)
  assert.equal((await fetch(`${apiUrl}/auth/logout`, { method: "POST", headers: writeHeaders })).status, 200)
  assert.equal((await fetch(sessionUrl, { headers })).status, 401)
  assert.equal((await fetch(`${apiUrl}/auth/session`, { headers: { cookie } })).status, 401)
  await db.query("INSERT INTO kinu.sessions VALUES ($1, $2, $3, $4)", [
    digest, JSON.stringify(identity), csrf, new Date(Date.now() - 1000),
  ])
  assert.equal((await fetch(sessionUrl, { headers })).status, 401)
  process.stdout.write("API and Agent-Native integration passed: identity, workspace isolation, CSRF, expiry, and revocation.\n")
} finally {
  await Promise.all(children.map(({ child }) => stop(child)))
  await socket.stop()
  await db.close()
}
