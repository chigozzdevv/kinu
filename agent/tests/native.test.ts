import assert from "node:assert/strict"
import test from "node:test"
import { readFile } from "node:fs/promises"
import { PGlite } from "@electric-sql/pglite"
import { withDbExec, type DbExec } from "@agent-native/core/db"
import { defineAppConfig } from "@agent-native/core/server"
import { saveMcpOAuthCredentials, readMcpOAuthCredentials } from "@agent-native/core/mcp-client"
import { grantOwner, mirrorIdentity, pendingOrg, status, disconnect, validateScopes } from "../server/grant"
import { decrypt, encrypt, hash } from "../server/storage"
import { bindSql } from "../server/sql"

defineAppConfig({ migration: { skipEnsureTables: true } })
process.env.APP_NAME = "kinu-agent"
process.env.KINU_AGENT_SECRETS_ENCRYPTION_KEY = "native-test-encryption-material-32bytes"

test("native SDK tokens are encrypted, scoped by workspace, and removable", async () => {
  const pg = await PGlite.create()
  await pg.exec(await readFile(new URL("../../infra/native.sql", import.meta.url), "utf8"))
  const exec: DbExec = {
    execute: async (statement) => {
      const query = typeof statement === "string" ? { sql: statement, args: [] } : statement
      const result = await pg.query(bindSql(query.sql, query.args?.length ?? 0).replace(/:p(\d+)/g, "$$$1"), query.args)
      return { rows: result.rows, rowsAffected: result.affectedRows ?? 0 }
    },
  }
  try {
    await withDbExec(exec, async () => {
      const session = { userId: "user_one", email: "owner@example.com", emailVerified: true, orgId: "org_one", orgRole: "administrator" }
      await mirrorIdentity(exec, session)
      await saveMcpOAuthCredentials({ ...grantOwner("org_one"), credentials: {
        serverUrl: "https://api.builder.io", clientInformation: { client_id: "test-client", issuer: "https://mcp.builder.io" },
        tokens: { access_token: "test-access-private", refresh_token: "test-refresh-private", token_type: "Bearer", scope: "builder:ai:invoke", issuer: "https://mcp.builder.io" },
      } })
      await exec.execute({ sql: "INSERT INTO builder_connections VALUES (?,?,?,?,?,NULL)", args: ["org_one", session.email, session.userId, "gpt-6-luna", Date.now()] })
      assert.equal((await status(exec, "org_one")).connected, true)
      assert.equal((await status(exec, "org_two")).connected, false)
      const stored = await pg.query<{ tokens: string }>("SELECT tokens FROM oauth_tokens")
      assert.match(stored.rows[0]!.tokens, /^v1:/)
      assert.equal(stored.rows[0]!.tokens.includes("test-access-private"), false)
      assert.equal((await readMcpOAuthCredentials(grantOwner("org_two"))), null)
      await disconnect(exec, "org_one")
      assert.equal((await status(exec, "org_one")).connected, false)
    })
  } finally { await pg.close() }
})

test("OAuth pending state is bound to the browser session and expiry", async () => {
  const pg = await PGlite.create()
  await pg.exec(await readFile(new URL("../../infra/native.sql", import.meta.url), "utf8"))
  const state = "a".repeat(43)
  await pg.query("INSERT INTO builder_pending VALUES ($1,$2,$3,$4,$5,$6)", [hash(state), hash("session-one"), "org_one", "user_one", Date.now() + 10000, encrypt({ verifier: "private" })])
  const exec: DbExec = { execute: async (statement) => {
    assert.notEqual(typeof statement, "string")
    const query = statement as { sql: string, args: unknown[] }
    const result = await pg.query(bindSql(query.sql, query.args.length).replace(/:p(\d+)/g, "$$$1"), query.args)
    return { rows: result.rows, rowsAffected: 0 }
  } }
  try {
    assert.equal(await pendingOrg(exec, state, "session-one"), "org_one")
    assert.equal(await pendingOrg(exec, state, "session-two"), null)
    await pg.exec("UPDATE builder_pending SET expires_at = 0")
    assert.equal(await pendingOrg(exec, state, "session-one"), null)
    const sealed = encrypt({ verifier: "private" })
    assert.equal(sealed.includes("private"), false)
    assert.deepEqual(decrypt(sealed), { verifier: "private" })
    assert.throws(() => decrypt(sealed.slice(0, -3) + "aaa"))
  } finally { await pg.close() }
})

test("SQL binding preserves quoted values and rejects mismatched parameters", () => {
  assert.equal(bindSql("SELECT '?', ? AS value, $$?$$ AS literal -- ?\n", 1), "SELECT '?', :p1 AS value, $$?$$ AS literal -- ?\n")
  assert.throws(() => bindSql("SELECT ?", 0), /count/)
  assert.throws(() => bindSql("SELECT $1", 1), /question marks/)
})

test("Builder consent permits AI and refresh without project or content access", () => {
  validateScopes("builder:ai:invoke offline_access")
  assert.throws(() => validateScopes("builder:ai:invoke builder:projects:write"))
  assert.throws(() => validateScopes("offline_access"))
})
