import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { MongoClient, type Db } from "mongodb"
import { MongoMemoryReplSet } from "mongodb-memory-server"
import { access, pendingOrg, status, disconnect, validateScopes } from "../server/grant"
import { decrypt, encrypt, hash } from "../server/storage"
import { builderEngine } from "../server/engine"

process.env.KINU_NATIVE_KEY = "native-test-encryption-material-32bytes"
let mongo: MongoMemoryReplSet, client: MongoClient, db: Db
before(async () => {
  mongo = await MongoMemoryReplSet.create({ replSet: { count: 1 } })
  client = await new MongoClient(mongo.getUri()).connect()
  db = client.db("native_test")
  await db.collection("connections").createIndex({ organisation_id: 1 }, { unique: true })
})
after(async () => { await client?.close(); await mongo?.stop() })

const grant = { serverUrl: "https://api.builder.io", clientInformation: { client_id: "test-client" },
  tokens: { access_token: "test-access-private", refresh_token: "test-refresh-private",
    token_type: "Bearer", scope: "builder:ai:invoke" } }

test("Builder grants are encrypted, bound to their workspace, and removable", async () => {
  const sealed = encrypt(grant, "org_one")
  await db.collection("connections").insertOne({ organisation_id: "org_one", owner_id: "user_one",
    owner_email: "owner@example.com", model: "gpt-6-luna", grant: sealed, revision: 0 })
  assert.equal((await status(db, "org_one")).connected, true)
  assert.equal((await status(db, "org_two")).connected, false)
  assert.equal(await access(db, "org_one"), "test-access-private")
  await assert.rejects(access(db, "org_two"))
  assert.equal(JSON.stringify(await db.collection("connections").findOne()).includes("test-access-private"), false)
  assert.throws(() => decrypt(sealed, "org_two"))
  await disconnect(db, "org_one")
  assert.equal((await status(db, "org_one")).connected, false)
})

test("pending consent is bound to the session and expires", async () => {
  const state = "a".repeat(43)
  await db.collection("pending").insertOne({ state_hash: hash(state), session_hash: hash("session-one"),
    organisation_id: "org_pending", expires_at: new Date(Date.now() + 10000) })
  assert.equal(await pendingOrg(db, state, "session-one"), "org_pending")
  assert.equal(await pendingOrg(db, state, "session-two"), null)
  await db.collection("pending").updateOne({}, { $set: { expires_at: new Date(0) } })
  assert.equal(await pendingOrg(db, state, "session-one"), null)
})

test("refresh rotates encrypted tokens with a revision and rejects invalid destinations", async () => {
  const expired = { ...grant, tokenExpiresAt: 1, discoveryState: { authorizationServerMetadata: {
    token_endpoint: "https://mcp.builder.io/token",
  } } }
  await db.collection("connections").insertOne({ organisation_id: "org_refresh", revision: 0,
    grant: encrypt(expired, "org_refresh") })
  const request: typeof fetch = async (input, init) => {
    assert.equal(String(input), "https://mcp.builder.io/token")
    assert.match(String(init?.body), /refresh_token=test-refresh-private/)
    return Response.json({ access_token: "fresh-private", expires_in: 3600, refresh_token: "fresh-refresh" })
  }
  assert.equal(await access(db, "org_refresh", request), "fresh-private")
  assert.equal(await access(db, "org_refresh", async () => { throw new Error("no refresh needed") }), "fresh-private")
  const row = await db.collection("connections").findOne({ organisation_id: "org_refresh" })
  assert.equal(row?.revision, 1)
  assert.equal(JSON.stringify(row).includes("fresh-private"), false)
})

test("Builder scope stays limited to AI invocation and refresh", () => {
  validateScopes("builder:ai:invoke offline_access")
  assert.throws(() => validateScopes("builder:ai:invoke builder:content:write"))
  assert.throws(() => validateScopes("offline_access"))
})

test("Agent-Native sends Mongo-backed OAuth to Builder without SQL or key credentials", async () => {
  const events = [
    ["message_start", { type: "message_start", message: { id: "msg_test", type: "message", role: "assistant",
      model: "gpt-6-luna", content: [], usage: { input_tokens: 2, output_tokens: 0 } } }],
    ["content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "hello" } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 1 } }],
    ["message_stop", { type: "message_stop" }],
  ].map(([name, body]) => `event: ${name}\ndata: ${JSON.stringify(body)}\n\n`).join("")
  let calls = 0
  const request: typeof fetch = async (input) => {
    calls++
    const call = input as Request
    assert.equal(call.url, "https://api.builder.io/agent-native/gateway/v1/messages")
    assert.equal(call.headers.get("authorization"), "Bearer oauth-private")
    assert.equal(call.headers.get("x-api-key"), null)
    const body = await call.json() as { model: string }
    assert.equal(body.model, "gpt-6-luna")
    return new Response(events, { headers: { "content-type": "text/event-stream" } })
  }
  const result = []
  for await (const event of builderEngine("oauth-private", request).stream({
    model: "gpt-6-luna", systemPrompt: "Reply briefly", messages: [{ role: "user", content: [{ type: "text", text: "hello" }] }],
    tools: [], abortSignal: new AbortController().signal, maxOutputTokens: 100,
  })) result.push(event)
  assert.equal(calls, 1)
  assert.equal(result.some((event) => event.type === "text-delta" && event.text === "hello"), true, JSON.stringify(result))
  assert.equal(JSON.stringify(result).includes("oauth-private"), false)
})
