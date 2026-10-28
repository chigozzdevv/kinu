import assert from "node:assert/strict"
import { createHash, randomBytes } from "node:crypto"
import { MongoClient } from "mongodb"
import { MongoMemoryReplSet } from "mongodb-memory-server"
import { freePort, root, service, stop } from "./fixture"

const mongo = await MongoMemoryReplSet.create({ replSet: { count: 1 } })
const client = await new MongoClient(mongo.getUri()).connect()
const db = client.db("kinu_integration")
const children: ReturnType<typeof service>[] = []
try {
  const apiUrl = `http://127.0.0.1:${await freePort()}`
  const agentUrl = `http://127.0.0.1:${await freePort()}`
  const publicUrl = "http://localhost:5173"
  const identity = {
    subject: "user_one",
    issuer: "http://localhost:5173/auth",
    email: "owner@example.com", email_verified: true, display_name: "Owner", connected_via: "Email and password",
  }
  const token = randomBytes(32).toString("base64url")
  const digest = createHash("sha256").update(token).digest("hex")
  const csrf = randomBytes(32).toString("base64url")
  await db.collection("sessions").insertOne({ token_hash: digest, identity, csrf_token: csrf,
    expires_at: new Date(Date.now() + 300_000).toISOString(), expires_on: new Date(Date.now() + 300_000) })
  const api = service("uv", ["run", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", new URL(apiUrl).port, "--no-access-log"], root, {
    ...process.env,
    UV_CACHE_DIR: ".cache/uv", UV_PYTHON_INSTALL_DIR: ".cache/python",
    KINU_ENVIRONMENT: "development", KINU_PUBLIC_URL: publicUrl,
    KINU_MONGO_URL: mongo.getUri(), KINU_MONGO_DATABASE: "kinu_integration",
    KINU_AUTH_SECRET: "integration-auth-secret-at-least-32-characters", KINU_TELEMETRY_ENABLED: "false",
  })
  children.push(api)
  await api.ready(`${apiUrl}/health/ready`)
  const agentEnv = { ...process.env }
  delete agentEnv.KINU_MONGO_URL
  const agent = service("node", [".output/server/index.mjs"], `${root}agent`, {
    ...agentEnv, KINU_ENVIRONMENT: "development", KINU_PUBLIC_URL: publicUrl, KINU_API_URL: apiUrl,
    KINU_NATIVE_MONGO_URL: mongo.getUri(), KINU_NATIVE_DATABASE: "native_integration",
    KINU_NATIVE_KEY: "integration-native-encryption-at-least-32-characters",
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
  const inventoryUrl = `${apiUrl}/v1/organisations/${workspace}/inventory`
  const graph = await fetch(`${inventoryUrl}/graph`, { headers: { cookie } })
  assert.equal(graph.status, 200)
  assert.deepEqual(await graph.json(), { credentials: [], services: [], bindings: [] })
  const connection = {
    id: "connection_browser", organisation_id: workspace, platform: "example", display_name: "Example",
    roles: ["provider"], interface: "browser", authorization: "browser-session",
    capabilities: ["browser.execute"], allowed_resources: ["example.com"], status: "setup-required",
    region: "eu-west-2", created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  }
  const saved = await fetch(`${inventoryUrl}/connections`, {
    method: "POST", headers: writeHeaders, body: JSON.stringify(connection),
  })
  assert.equal(saved.status, 201, await saved.text())
  const stored = await fetch(`${inventoryUrl}/connections`, { headers: { cookie } })
  assert.equal(stored.status, 200)
  const records = await stored.json() as { id: string }[]
  assert.equal(records[0]?.id, connection.id)
  assert.equal(await db.collection("inventory").countDocuments({ organisation_id: workspace, kind: "connection" }), 1)
  assert.equal((await fetch(`${apiUrl}/v1/organisations/org_other/inventory/connections`, { headers: { cookie } })).status, 403)
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
  const tools = await fetch(`${agentUrl}/_agent-native/kinu/tools`, { headers })
  assert.equal(tools.status, 200)
  assert.deepEqual((await tools.json() as { name: string }[]).map((item) => item.name), ["context", "rotation", "alert"])
  const capabilityUrl = `${agentUrl}/_agent-native/kinu/actions/context`
  const nativeHeaders = { ...writeHeaders, "x-kinu-workspace": workspace }
  const context = await fetch(capabilityUrl, { method: "POST", headers: nativeHeaders, body: JSON.stringify({ resource: "inventory" }) })
  assert.equal(context.status, 200, await context.text())
  assert.equal((await fetch(capabilityUrl, { method: "POST", headers, body: JSON.stringify({ resource: "inventory" }) })).status, 403)
  const forged = await fetch(capabilityUrl, { method: "POST", headers: { ...nativeHeaders, "x-csrf-token": "forged" }, body: JSON.stringify({ resource: "inventory" }) })
  assert.equal(forged.status, 403)
  assert.equal((await fetch(`${agentUrl}/_agent-native/kinu/builder/test`, { method: "POST", headers: nativeHeaders, body: "{}" })).status, 404)
  assert.equal((await fetch(`${agentUrl}/_agent-native/kinu/actions/assessment`, { method: "POST", headers: nativeHeaders, body: "{}" })).status, 404)
  assert.equal((await fetch(`${agentUrl}/_agent-native/kinu/builder/connect`, { method: "POST", headers: { ...nativeHeaders, origin: "https://other.example" }, body: "{}" })).status, 403)
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
  await db.collection("members").updateOne({ organisation_id: workspace }, { $set: { "data.enabled": false } })
  assert.equal((await fetch(sessionUrl, { headers })).status, 403)
  await db.collection("members").updateOne({ organisation_id: workspace }, { $set: { "data.enabled": true } })
  assert.equal((await fetch(sessionUrl, { headers })).status, 200)
  assert.equal((await fetch(`${apiUrl}/v1/organisations`, { method: "POST", headers: { cookie, "content-type": "application/json" }, body: JSON.stringify({ name: "Forged" }) })).status, 403)
  assert.equal((await fetch(`${apiUrl}/auth/logout`, { method: "POST", headers: writeHeaders })).status, 200)
  assert.equal((await fetch(sessionUrl, { headers })).status, 401)
  assert.equal((await fetch(`${apiUrl}/auth/session`, { headers: { cookie } })).status, 401)
  await db.collection("sessions").insertOne({ token_hash: digest, identity, csrf_token: csrf,
    expires_at: new Date(Date.now() - 1000).toISOString(), expires_on: new Date(Date.now() - 1000) })
  assert.equal((await fetch(sessionUrl, { headers })).status, 401)
  process.stdout.write("API and Agent-Native integration passed: inventory persistence, identity, workspace isolation, CSRF, expiry, and revocation.\n")
} finally {
  await Promise.all(children.map(({ child }) => stop(child)))
  await client.close()
  await mongo.stop()
}
