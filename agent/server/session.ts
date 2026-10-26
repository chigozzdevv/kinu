import type { AuthSession } from "@agent-native/core/server"
import { HTTPError } from "h3"
import { z } from "zod"
import type { Config } from "./config"

const workspaceId = z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/)
const sessionSchema = z.object({
  identity: z.object({
    uid: z.string().min(1),
    email: z.email(),
    emailVerified: z.literal(true),
    displayName: z.string().nullable(),
  }),
  workspace: z.object({
    id: workspaceId,
    role: z.enum(["administrator", "operator", "viewer"]),
  }).nullable(),
  expires_at: z.iso.datetime({ offset: true }),
})

export function sessionCookie(headers: Headers, config: Config): string | null {
  const values = (headers.get("cookie") ?? "").split(";").flatMap((part) => {
    const separator = part.indexOf("=")
    return separator > 0 && part.slice(0, separator).trim() === config.cookieName
      ? [part.slice(separator + 1).trim()] : []
  })
  if (values.length !== 1 || !/^[A-Za-z0-9_-]{43}$/.test(values[0] ?? "")) return null
  return `${config.cookieName}=${values[0]}`
}

export async function resolveSession(
  headers: Headers, config: Config, request: typeof fetch = fetch,
): Promise<AuthSession | null> {
  if (headers.has("authorization")) throw new HTTPError({ status: 401 })
  const cookie = sessionCookie(headers, config)
  if (!cookie) return null
  const workspace = headers.get("x-kinu-workspace")
  if (!workspace || !workspaceId.safeParse(workspace).success) {
    throw new HTTPError({ status: 400, message: "Select a valid workspace" })
  }
  let response: Response
  try {
    response = await request(`${config.apiUrl}/auth/session`, {
      headers: { cookie, ...(workspace ? { "x-kinu-workspace": workspace } : {}) },
      redirect: "error",
      signal: AbortSignal.timeout(5000),
    })
  } catch {
    throw new HTTPError({ status: 503, message: "Session service is unavailable" })
  }
  if (response.status === 401) return null
  if (response.status === 403) throw new HTTPError({ status: 403, message: "Workspace access denied" })
  if (!response.ok) throw new HTTPError({ status: 503, message: "Session service is unavailable" })
  const parsed = sessionSchema.safeParse(await response.json().catch(() => null))
  if (!parsed.success) throw new HTTPError({ status: 503, message: "Session response is invalid" })
  if (Date.parse(parsed.data.expires_at) <= Date.now()) return null
  if (workspace && parsed.data.workspace?.id !== workspace) {
    throw new HTTPError({ status: 403, message: "Workspace access denied" })
  }
  return {
    userId: parsed.data.identity.uid,
    email: parsed.data.identity.email,
    emailVerified: true,
    name: parsed.data.identity.displayName ?? undefined,
    orgId: parsed.data.workspace?.id,
    orgRole: parsed.data.workspace?.role,
  }
}
