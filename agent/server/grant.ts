import { randomBytes } from "node:crypto"
import type { DbExec } from "@agent-native/core/db"
import {
  finishMcpOAuthAuthorization, readMcpOAuthCredentials,
  saveMcpOAuthCredentials, startMcpOAuthAuthorization, type McpOAuthStartResult,
} from "@agent-native/core/mcp-client"
import { revokeOAuthCredential } from "@agent-native/core/oauth-tokens"
import { BUILDER_DEFAULT_MODEL, BUILDER_SUPPORTED_MODELS } from "@agent-native/core/agent/engine"
import type { AuthSession } from "@agent-native/core/server"
import { HTTPError } from "h3"
import { decrypt, encrypt, hash } from "./storage"

export const resource = "https://api.builder.io"
export const scope = "builder:ai:invoke"
export const callbackPath = "/_agent-native/kinu/builder/callback"
const metadata = "https://mcp.builder.io/.well-known/oauth-protected-resource/api"

export function grantOwner(orgId: string) {
  return { key: `builder-general-resource-v1:o:${hash(orgId)}`, scope: "org" as const, scopeId: orgId, serverUrl: resource }
}

export interface Connection { organisation_id: string, owner_email: string, owner_id: string, model: string, connected_at: number, verified_at: number | null }

export async function connection(db: DbExec, orgId: string): Promise<Connection | null> {
  const { rows } = await db.execute({ sql: "SELECT * FROM builder_connections WHERE organisation_id = ?", args: [orgId] })
  return rows[0] as Connection | undefined ?? null
}

export async function status(db: DbExec, orgId: string) {
  const current = await connection(db, orgId)
  const grant = await readMcpOAuthCredentials(grantOwner(orgId))
  const connected = Boolean(current && grant && !grant.oauthLifecycle?.reconnectReason && grant.tokens.scope?.split(/\s+/).includes(scope))
  return { connected, model: current?.model ?? BUILDER_DEFAULT_MODEL, verified_at: connected ? current?.verified_at ?? null : null, models: [...BUILDER_SUPPORTED_MODELS].filter((model) => model !== "auto") }
}

export async function begin(db: DbExec, session: AuthSession, cookie: string, origin: string): Promise<string> {
  const state = randomBytes(32).toString("base64url")
  const result = await startMcpOAuthAuthorization({
    serverUrl: resource, redirectUrl: origin + callbackPath, state, scope, resourceMetadataUrl: metadata,
  })
  const target = new URL(result.authorizationUrl)
  if (target.protocol !== "https:" || target.hostname !== "builder.io" && !target.hostname.endsWith(".builder.io")) {
    throw new Error("Builder returned an unexpected authorization destination")
  }
  validateScopes(target.searchParams.get("scope") ?? "")
  await db.execute({ sql: "DELETE FROM builder_pending WHERE expires_at < ?", args: [Date.now()] })
  await db.execute({
    sql: "INSERT INTO builder_pending VALUES (?, ?, ?, ?, ?, ?)",
    args: [hash(state), hash(cookie), session.orgId, session.userId, Date.now() + 600_000, encrypt({ ...result, authorizationUrl: result.authorizationUrl.toString() })],
  })
  return target.toString()
}

export function validateScopes(value: string): void {
  const scopes = value.split(/\s+/)
  if (!scopes.includes(scope) || scopes.some((item) => ![scope, "offline_access"].includes(item))) {
    throw new Error("Builder requested permissions outside AI invocation and connection refresh")
  }
}

export async function pendingOrg(db: DbExec, state: string, cookie: string): Promise<string | null> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(state)) return null
  const { rows } = await db.execute({
    sql: "SELECT organisation_id FROM builder_pending WHERE state_hash = ? AND session_hash = ? AND expires_at > ?",
    args: [hash(state), hash(cookie), Date.now()],
  })
  return typeof rows[0]?.organisation_id === "string" ? rows[0].organisation_id : null
}

export async function finish(db: DbExec, session: AuthSession, cookie: string, url: URL): Promise<void> {
  const { rows } = await db.execute({
    sql: "DELETE FROM builder_pending WHERE state_hash = ? AND session_hash = ? AND organisation_id = ? AND user_id = ? AND expires_at > ? RETURNING data",
    args: [hash(url.searchParams.get("state") ?? ""), hash(cookie), session.orgId, session.userId, Date.now()],
  })
  if (!rows[0]) throw new HTTPError({ status: 400, message: "Builder connection expired or was already used" })
  const saved = decrypt(rows[0].data as string) as Omit<McpOAuthStartResult, "authorizationUrl">
  const code = url.searchParams.get("code")
  if (!code || url.searchParams.has("error")) throw new HTTPError({ status: 400, message: "Builder connection was not approved" })
  const { credentials } = await finishMcpOAuthAuthorization({
    serverUrl: resource, redirectUrl: url.origin + callbackPath, state: saved.state,
    codeVerifier: saved.codeVerifier, clientInformation: saved.clientInformation, discoveryState: saved.discoveryState,
    authorizationCode: code, iss: url.searchParams.get("iss") ?? undefined,
  })
  const scopes = credentials.tokens.scope?.split(/\s+/) ?? [scope]
  if (!scopes.includes(scope)) throw new Error("Builder did not grant AI invocation")
  credentials.tokens.scope = scopes.join(" ")
  await saveMcpOAuthCredentials({ ...grantOwner(session.orgId!), credentials })
  await db.execute({
    sql: "INSERT INTO builder_connections VALUES (?, ?, ?, ?, ?, NULL) ON CONFLICT (organisation_id) DO UPDATE SET owner_email = excluded.owner_email, owner_id = excluded.owner_id, connected_at = excluded.connected_at, verified_at = NULL",
    args: [session.orgId, session.email.toLowerCase(), session.userId, BUILDER_DEFAULT_MODEL, Date.now()],
  })
  await mirrorIdentity(db, session)
}

export async function mirrorIdentity(db: DbExec, session: AuthSession): Promise<void> {
  await db.execute({
    sql: "INSERT INTO org_members (org_id,email,role) VALUES (?,?,?) ON CONFLICT (org_id,email) DO UPDATE SET role = excluded.role",
    args: [session.orgId, session.email.toLowerCase(), session.orgRole === "administrator" ? "admin" : "member"],
  })
  await db.execute({
    sql: "INSERT INTO settings (key,value,updated_at) VALUES (?,?,?) ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
    args: [`o:${session.orgId}:restrict-personal-provider-keys`, JSON.stringify({ restricted: true }), Date.now()],
  })
}

export async function disconnect(db: DbExec, orgId: string): Promise<void> {
  const owner = grantOwner(orgId)
  await revokeOAuthCredential({ provider: "mcp", accountId: owner.key, resource: new URL(resource).toString(), owner: { scope: "org", id: orgId } }, {
    legacyAccountKey: true,
    revoke: async ({ credential }) => {
      const stored = credential as Awaited<ReturnType<typeof readMcpOAuthCredentials>>
      const metadata = stored?.discoveryState?.authorizationServerMetadata
      const endpoint = metadata && "revocation_endpoint" in metadata ? metadata.revocation_endpoint : undefined
      if (typeof endpoint !== "string" || !stored) return "unsupported"
      const url = new URL(endpoint)
      if (url.protocol !== "https:" || !url.hostname.endsWith(".builder.io")) throw new Error("Builder revocation destination is invalid")
      const response = await fetch(url, {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(8000),
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ token: stored.tokens.refresh_token ?? stored.tokens.access_token, token_type_hint: stored.tokens.refresh_token ? "refresh_token" : "access_token", client_id: stored.clientInformation.client_id }),
      })
      await response.body?.cancel()
      if (!response.ok) throw new Error("Builder revocation failed")
      return "succeeded"
    },
  })
  await db.execute({ sql: "DELETE FROM builder_connections WHERE organisation_id = ?", args: [orgId] })
}
