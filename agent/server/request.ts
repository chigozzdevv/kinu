import type { ActionRunContext } from "@agent-native/core/action"
import { HTTPError } from "h3"
import { loadConfig } from "./config"
import { sessionCookie } from "./session"

export async function applicationRequest(
  context: ActionRunContext | undefined, path: string,
  options: { method: "POST", idempotency: string, body: unknown } | undefined = undefined,
): Promise<unknown> {
  const config = loadConfig()
  const headers = context?.requestHeaders
  const cookie = headers && sessionCookie(headers, config)
  if (!cookie || !context?.orgId || !/^[a-z][a-z0-9_-]{2,127}$/.test(context.orgId)) {
    throw new HTTPError({ status: 401 })
  }
  const response = await fetch(`${config.apiUrl}/v1/organisations/${context.orgId}/${path}`, {
    method: options?.method ?? "GET", redirect: "error", signal: AbortSignal.timeout(25_000),
    headers: {
      cookie, "x-kinu-workspace": context.orgId,
      ...(options ? {
        "content-type": "application/json", "idempotency-key": options.idempotency,
        "x-csrf-token": headers.get("x-csrf-token") ?? "", origin: headers.get("origin") ?? "",
      } : {}),
    },
    ...(options ? { body: JSON.stringify(options.body) } : {}),
  })
  if (!response.ok) {
    let message = "Application capability was rejected"
    try {
      const body = await response.json() as { message?: unknown }
      if (typeof body.message === "string" && body.message) message = body.message
    } catch {}
    console.error("kinu capability rejected", response.status, path, message)
    throw new HTTPError({ status: response.status, message })
  }
  return response.json()
}
