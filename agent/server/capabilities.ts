import type { AuthSession } from "@agent-native/core/server"
import type { ActionRunContext } from "@agent-native/core/action"
import { HTTPError } from "h3"
import { z } from "zod"
import contextAction, { schema as contextSchema } from "../actions/context"
import rotationAction, { schema as rotationSchema } from "../actions/rotation"
import alertAction, { schema as alertSchema } from "../actions/alert"
import type { Config } from "./config"
import { sessionCookie } from "./session"

export function capabilityPath(path: string, method: string): boolean {
  return (path === "/_agent-native/kinu/tools" && method === "GET") ||
    (/^\/_agent-native\/kinu\/actions\/(context|rotation|alert)$/.test(path) && method === "POST")
}

export async function capabilityResponse(request: Request, session: AuthSession, config: Config): Promise<Response> {
  const path = new URL(request.url).pathname
  if (path === "/_agent-native/kinu/tools") {
    return Response.json([
      { name: "context", description: "Read workspace inventory, incidents, and runs.", inputSchema: z.toJSONSchema(contextSchema) },
      { name: "rotation", description: "Request a rotation under workspace policy.", inputSchema: z.toJSONSchema(rotationSchema) },
      { name: "alert", description: "Queue a labelled demo email fixture.", inputSchema: z.toJSONSchema(alertSchema) },
    ])
  }
  const headers = await checkWrite(request, session, config)
  const body = await boundedBody(request)
  const context: ActionRunContext = { caller: "frontend", orgId: session.orgId, requestHeaders: headers }
  if (path.endsWith("/alert")) {
    const parsed = alertSchema.safeParse(body)
    if (!parsed.success) throw new HTTPError({ status: 422, message: "Capability input is invalid" })
    return Response.json(await alertAction.run(parsed.data, context))
  }
  const parsed = path.endsWith("/context") ? contextSchema.safeParse(body) : rotationSchema.safeParse(body)
  if (!parsed.success) throw new HTTPError({ status: 422, message: "Capability input is invalid" })
  const value = path.endsWith("/context")
    ? await contextAction.run(contextSchema.parse(parsed.data), context)
    : await rotationAction.run(rotationSchema.parse(parsed.data), context)
  return Response.json(value)
}

export async function checkWrite(request: Request, session: AuthSession, config: Config): Promise<Headers> {
  if (request.headers.get("origin") !== config.publicUrl) throw new HTTPError({ status: 403 })
  const headers = new Headers({
    cookie: sessionCookie(request.headers, config) ?? "",
    origin: config.publicUrl, "x-csrf-token": request.headers.get("x-csrf-token") ?? "",
    "x-kinu-workspace": session.orgId ?? "",
  })
  const checked = await fetch(`${config.apiUrl}/auth/check`, {
    method: "POST", headers, redirect: "error", signal: AbortSignal.timeout(25_000),
  })
  if (!checked.ok) throw new HTTPError({ status: checked.status })
  return headers
}

export async function boundedBody(request: Request): Promise<unknown> {
  const reader = request.body?.getReader()
  if (!reader) throw new HTTPError({ status: 400 })
  const chunks: Uint8Array[] = []
  let size = 0
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > 16_384) throw new HTTPError({ status: 413 })
      chunks.push(value)
    }
    return JSON.parse(Buffer.concat(chunks).toString())
  } catch (error) {
    if (error instanceof HTTPError) throw error
    throw new HTTPError({ status: 400, message: "Capability input must be JSON" })
  } finally { await reader.cancel() }
}
