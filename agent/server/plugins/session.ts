import { createAuthPlugin, defineAppConfig, type AuthSession } from "@agent-native/core/server"
import { HTTPError } from "h3"
import { definePlugin } from "nitro"
import { loadConfig } from "../config"
import { resolveSession, sessionCookie } from "../session"
import { capabilityPath, capabilityResponse } from "../capabilities"
import { builderCallback, builderPath, builderResponse } from "../builder"
import { callbackPath } from "../grant"
import { withStorage } from "../storage"

export default definePlugin((app) => {
  const config = loadConfig()
  const sessions = new WeakMap<Request, AuthSession>()
  defineAppConfig({
    migration: { skipEnsureTables: true },
    observability: { capturePrompts: false, captureToolArgs: false, captureToolResults: false },
    plugins: {
      disabled: [
        "agent-chat", "auth", "context-xray", "core-routes", "integrations",
        "observational-memory", "onboarding", "org", "resources", "sentry", "terminal",
      ],
    },
  })
  createAuthPlugin({
    mountGoogleOAuthRoutes: false,
    publicPaths: ["/health/live"],
    getSession: async (event) => {
      const session = sessions.get(event.req)
      if (!session) throw new HTTPError({ status: 401 })
      return session
    },
  })(app)
  const handle = app.fetch
  app.fetch = async (request) => {
    let response: Response
    try {
      const url = new URL(request.url)
      if (url.pathname === callbackPath && request.method === "GET") {
        response = await builderCallback(request, config)
        response.headers.set("cache-control", "no-store")
        response.headers.set("referrer-policy", "no-referrer")
        return response
      }
      if (url.pathname !== "/health/live" || request.method !== "GET") {
        if (url.search || !((url.pathname === "/_agent-native/auth/session" && request.method === "GET") || capabilityPath(url.pathname, request.method) || builderPath(url.pathname, request.method))) {
          throw new HTTPError({ status: 404 })
        }
        const session = await resolveSession(request.headers, config)
        if (!session) throw new HTTPError({ status: 401 })
        sessions.set(request, session)
        request.headers.set("cookie", sessionCookie(request.headers, config) ?? "")
        if (builderPath(url.pathname, request.method)) {
          response = await builderResponse(request, session, config)
          response.headers.set("cache-control", "no-store")
          response.headers.set("referrer-policy", "no-referrer")
          return response
        }
        if (capabilityPath(url.pathname, request.method)) {
          response = await capabilityResponse(request, session, config)
          response.headers.set("cache-control", "no-store")
          response.headers.set("referrer-policy", "no-referrer")
          return response
        }
      }
      response = url.pathname === "/_agent-native/auth/session" && process.env.KINU_ENVIRONMENT === "production"
        ? await withStorage(async () => handle(request))
        : await handle(request)
    } catch (error) {
      response = error instanceof HTTPError
        ? Response.json({ error: error.message }, { status: error.status })
        : Response.json({ error: "Agent service is temporarily unavailable" }, { status: 503 })
    }
    response.headers.set("cache-control", "no-store")
    response.headers.set("referrer-policy", "no-referrer")
    return response
  }
})
