import { randomBytes } from "node:crypto"
import type { Db } from "mongodb"
import {
  finishMcpOAuthAuthorization, startMcpOAuthAuthorization,
  type McpOAuthStartResult, type McpOAuthCredentialBundle,
} from "@agent-native/core/mcp-client"
import { BUILDER_DEFAULT_MODEL, BUILDER_SUPPORTED_MODELS } from "@agent-native/core/agent/engine"
import type { AuthSession } from "@agent-native/core/server"
import { HTTPError } from "h3"
import { decrypt, encrypt, hash } from "./storage"

export const resource = "https://api.builder.io"
export const scope = "builder:ai:invoke"
export const callbackPath = "/_agent-native/kinu/builder/callback"
const metadata = "https://mcp.builder.io/.well-known/oauth-protected-resource/api"

export interface Connection {
  organisation_id: string, owner_email: string, owner_id: string, model: string,
  connected_at: number, verified_at: number | null, grant: string, revision: number,
  reconnect?: boolean, lease_until?: Date, lease_owner?: string,
}

export async function connection(db: Db, orgId: string): Promise<Connection | null> {
  return db.collection<Connection>("connections").findOne({ organisation_id: orgId })
}

export async function status(db: Db, orgId: string) {
  const current = await connection(db, orgId)
  return { connected: Boolean(current && !current.reconnect), model: current?.model ?? BUILDER_DEFAULT_MODEL,
    verified_at: current?.verified_at ?? null, models: [...BUILDER_SUPPORTED_MODELS].filter((model) => model !== "auto") }
}

export async function begin(db: Db, session: AuthSession, cookie: string, origin: string): Promise<string> {
  const state = randomBytes(32).toString("base64url")
  const result = await startMcpOAuthAuthorization({
    serverUrl: resource, redirectUrl: origin + callbackPath, state, scope, resourceMetadataUrl: metadata,
  })
  const target = new URL(result.authorizationUrl)
  builderUrl(target)
  validateScopes(target.searchParams.get("scope") ?? "")
  await db.collection("pending").insertOne({
    state_hash: hash(state), session_hash: hash(cookie), organisation_id: session.orgId,
    user_id: session.userId, expires_at: new Date(Date.now() + 600_000),
    data: encrypt({ ...result, authorizationUrl: result.authorizationUrl.toString() }, `pending:${state}`),
  })
  return target.toString()
}

export function validateScopes(value: string): void {
  const scopes = value.split(/\s+/)
  if (!scopes.includes(scope) || scopes.some((item) => ![scope, "offline_access"].includes(item))) {
    throw new Error("Builder requested permissions outside AI invocation and connection refresh")
  }
}

export async function pendingOrg(db: Db, state: string, cookie: string): Promise<string | null> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(state)) return null
  const row = await db.collection("pending").findOne({
    state_hash: hash(state), session_hash: hash(cookie), expires_at: { $gt: new Date() },
  })
  return typeof row?.organisation_id === "string" ? row.organisation_id : null
}

export async function finish(db: Db, session: AuthSession, cookie: string, url: URL): Promise<void> {
  const state = url.searchParams.get("state") ?? ""
  const row = await db.collection("pending").findOneAndDelete({
    state_hash: hash(state), session_hash: hash(cookie), organisation_id: session.orgId,
    user_id: session.userId, expires_at: { $gt: new Date() },
  })
  if (!row) throw new HTTPError({ status: 400, message: "Builder connection expired or was already used" })
  const saved = decrypt<Omit<McpOAuthStartResult, "authorizationUrl">>(row.data as string, `pending:${state}`)
  const code = url.searchParams.get("code")
  if (!code || url.searchParams.has("error")) throw new HTTPError({ status: 400, message: "Builder connection was not approved" })
  const { credentials } = await finishMcpOAuthAuthorization({
    serverUrl: resource, redirectUrl: url.origin + callbackPath, state: saved.state,
    codeVerifier: saved.codeVerifier, clientInformation: saved.clientInformation, discoveryState: saved.discoveryState,
    authorizationCode: code, iss: url.searchParams.get("iss") ?? undefined,
  })
  validateScopes(credentials.tokens.scope ?? scope)
  await db.collection<Connection>("connections").updateOne({ organisation_id: session.orgId }, {
    $set: { owner_email: session.email.toLowerCase(), owner_id: session.userId, connected_at: Date.now(),
      verified_at: null, model: BUILDER_DEFAULT_MODEL, grant: encrypt(credentials, session.orgId!), reconnect: false },
    $inc: { revision: 1 }, $unset: { lease_owner: "", lease_until: "" },
  }, { upsert: true })
}

export async function access(db: Db, orgId: string, request: typeof fetch = fetch): Promise<string> {
  const current = await connection(db, orgId)
  if (!current || current.reconnect) throw new Error("Reconnect Builder.io")
  const grant = decrypt<McpOAuthCredentialBundle>(current.grant, orgId)
  if (!grant.tokenExpiresAt || grant.tokenExpiresAt > Date.now() + 60_000) return grant.tokens.access_token
  const owner = randomBytes(16).toString("hex")
  const locked = await db.collection<Connection>("connections").findOneAndUpdate({
    organisation_id: orgId, revision: current.revision,
    $or: [{ lease_until: { $exists: false } }, { lease_until: { $lte: new Date() } }],
  }, { $set: { lease_owner: owner, lease_until: new Date(Date.now() + 30_000) } })
  if (!locked) throw new Error("Builder connection is refreshing; retry shortly")
  const query = { organisation_id: orgId, revision: current.revision, lease_owner: owner }
  try {
    const endpoint = grant.discoveryState?.authorizationServerMetadata?.token_endpoint
    if (!endpoint || !grant.tokens.refresh_token) throw new Error("Reconnect Builder.io")
    builderUrl(new URL(endpoint))
    const body = new URLSearchParams({ grant_type: "refresh_token", refresh_token: grant.tokens.refresh_token,
      client_id: grant.clientInformation.client_id, resource })
    if (grant.clientInformation.client_secret) body.set("client_secret", grant.clientInformation.client_secret)
    const response = await request(endpoint, { method: "POST", redirect: "error", signal: AbortSignal.timeout(10_000),
      headers: { "content-type": "application/x-www-form-urlencoded" }, body })
    if (!response.ok) {
      if (response.status === 400 || response.status === 401) await db.collection("connections").updateOne(query, { $set: { reconnect: true } })
      throw new Error("Builder connection could not refresh")
    }
    const tokens = await response.json() as Record<string, unknown>
    if (typeof tokens.access_token !== "string" || !tokens.access_token || (!Number.isFinite(Number(tokens.expires_in)) || Number(tokens.expires_in) <= 0)) throw new Error("Builder returned invalid credentials")
    validateScopes(typeof tokens.scope === "string" ? tokens.scope : grant.tokens.scope ?? scope)
    const updated = { ...grant, tokens: { ...grant.tokens, ...tokens }, tokenExpiresAt: Date.now() + Number(tokens.expires_in) * 1000 }
    const saved = await db.collection("connections").updateOne(query,
      { $set: { grant: encrypt(updated, orgId) }, $inc: { revision: 1 }, $unset: { lease_owner: "", lease_until: "" } })
    if (!saved.matchedCount) throw new Error("Builder connection changed during refresh")
    return tokens.access_token
  } finally {
    await db.collection("connections").updateOne(query, { $unset: { lease_owner: "", lease_until: "" } })
  }
}

export async function disconnect(db: Db, orgId: string): Promise<void> {
  const current = await db.collection<Connection>("connections").findOneAndDelete({ organisation_id: orgId })
  if (!current) return
  const stored = decrypt<McpOAuthCredentialBundle>(current.grant, orgId)
  const metadata = stored.discoveryState?.authorizationServerMetadata
  const endpoint = metadata && "revocation_endpoint" in metadata ? metadata.revocation_endpoint : undefined
  if (typeof endpoint === "string") {
    builderUrl(new URL(endpoint))
    await fetch(endpoint, { method: "POST", redirect: "error", signal: AbortSignal.timeout(8000),
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: stored.tokens.refresh_token ?? stored.tokens.access_token,
        client_id: stored.clientInformation.client_id }),
    }).then((response) => response.body?.cancel()).catch(() => undefined)
  }
}

function builderUrl(url: URL): void {
  if (url.protocol !== "https:" || url.username || url.password ||
      url.hostname !== "builder.io" && !url.hostname.endsWith(".builder.io")) {
    throw new Error("Builder returned an unexpected authorization destination")
  }
}
