import { BUILDER_SUPPORTED_MODELS } from "@agent-native/core/agent/engine"
import type { AuthSession } from "@agent-native/core/server"
import { HTTPError } from "h3"
import { z } from "zod"
import type { Config } from "./config"
import { resolveSession, sessionCookie } from "./session"
import { begin, callbackPath, disconnect, finish, pendingOrg, status } from "./grant"
import { withStorage } from "./storage"
import { assessment } from "./assessment"
import { checkWrite, boundedBody } from "./capabilities"

export function builderPath(path: string, method: string): boolean {
  return method === "GET" && (path === callbackPath || path === "/_agent-native/kinu/builder/status") ||
    method === "POST" && /^\/_agent-native\/kinu\/builder\/(connect|disconnect|model|test)$/.test(path)
}

export async function builderCallback(request: Request, config: Config): Promise<Response> {
  const url = new URL(request.url)
  const names = [...url.searchParams.keys()]
  if (url.origin !== config.publicUrl || names.some((name) => !["state", "code", "iss", "error", "error_description"].includes(name)) || names.some((name) => url.searchParams.getAll(name).length !== 1)) throw new HTTPError({ status: 400 })
  const cookie = sessionCookie(request.headers, config)
  if (!cookie) throw new HTTPError({ status: 401 })
  return withStorage(async (db) => {
    const org = await pendingOrg(db, url.searchParams.get("state") ?? "", cookie)
    if (!org) throw new HTTPError({ status: 400, message: "Builder connection state is invalid or expired" })
    const headers = new Headers(request.headers)
    headers.set("x-kinu-workspace", org)
    const session = await resolveSession(headers, config)
    administrator(session)
    await finish(db, session!, cookie, url)
    return new Response(null, { status: 303, headers: { location: config.publicUrl + "/dashboard/settings?tab=ai" } })
  })
}

export async function builderResponse(request: Request, session: AuthSession, config: Config): Promise<Response> {
  if (request.method === "GET") return withStorage(async (db) => Response.json(await status(db, session.orgId!)))
  administrator(session)
  await checkWrite(request, session, config)
  const path = new URL(request.url).pathname
  if (path.endsWith("/test")) {
    const value = await assessment(session.orgId!, { stage: "investigate", kind: "credential-security-review", confidence: "low", candidate_count: 0, policy_allows_rotation: false })
    return Response.json({ ok: true, decision: value.decision })
  }
  return withStorage(async (db) => {
    if (path.endsWith("/connect")) return Response.json({ authorization_url: await begin(db, session, sessionCookie(request.headers, config)!, config.publicUrl) })
    if (path.endsWith("/disconnect")) { await disconnect(db, session.orgId!); return Response.json({ connected: false }) }
    const schema = z.strictObject({ model: z.enum(BUILDER_SUPPORTED_MODELS).refine((model) => model !== "auto") })
    const parsed = schema.safeParse(await boundedBody(request))
    if (!parsed.success) throw new HTTPError({ status: 422, message: "Select an available model" })
    const result = await db.execute({ sql: "UPDATE builder_connections SET model = ?, verified_at = NULL WHERE organisation_id = ?", args: [parsed.data.model, session.orgId] })
    if (!result.rowsAffected) throw new HTTPError({ status: 409, message: "Connect Builder.io first" })
    return Response.json(await status(db, session.orgId!))
  })
}

function administrator(session: AuthSession | null) {
  if (!session) throw new HTTPError({ status: 401 })
  if (!session.orgId || session.orgRole !== "administrator") throw new HTTPError({ status: 403, message: "A workspace administrator must manage the AI connection" })
}
