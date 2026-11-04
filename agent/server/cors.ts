import type { Config } from "./config"

const allowedHeaders = ["content-type", "x-csrf-token", "x-kinu-workspace", "idempotency-key"]

export async function withCors(request: Request, config: Config, next: () => Promise<Response>): Promise<Response> {
  if (!new URL(request.url).pathname.startsWith("/_agent-native/")) return next()
  const origin = request.headers.get("origin")
  if (request.method === "OPTIONS") {
    const method = request.headers.get("access-control-request-method") ?? ""
    const requested = (request.headers.get("access-control-request-headers") ?? "").toLowerCase().split(",").map((item) => item.trim()).filter(Boolean)
    if (origin !== config.publicUrl || !["GET", "POST"].includes(method) || requested.some((item) => !allowedHeaders.includes(item))) {
      return new Response(null, {status: 403})
    }
    return new Response(null, {status: 204, headers: {
      "access-control-allow-origin": origin,
      "access-control-allow-credentials": "true",
      "access-control-allow-methods": "GET, POST",
      "access-control-allow-headers": allowedHeaders.join(", "),
      "vary": "Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
      "cache-control": "no-store",
    }})
  }
  const response = await next()
  if (origin === config.publicUrl) {
    response.headers.set("access-control-allow-origin", origin)
    response.headers.set("access-control-allow-credentials", "true")
    response.headers.append("vary", "Origin")
  }
  return response
}
