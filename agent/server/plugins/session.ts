import { createAuthPlugin, defineAppConfig, type AuthSession } from "@agent-native/core/server"
import { HTTPError } from "h3"
import { definePlugin } from "nitro"
import { loadConfig } from "../config"
import { resolveSession, sessionCookie } from "../session"

export default definePlugin((app) => {
  const config = loadConfig()
  const sessions = new WeakMap<Request, AuthSession>()
  defineAppConfig({
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
      if (url.pathname !== "/health/live" || request.method !== "GET") {
        if (url.pathname !== "/_agent-native/auth/session" || request.method !== "GET" || url.search) {
          throw new HTTPError({ status: 404 })
        }
        const session = await resolveSession(request.headers, config)
        if (!session) throw new HTTPError({ status: 401 })
        sessions.set(request, session)
        request.headers.set("cookie", sessionCookie(request.headers, config) ?? "")
      }
      response = await handle(request)
    } catch (error) {
      if (!(error instanceof HTTPError)) throw error
      response = Response.json({ error: error.message }, { status: error.status })
    }
    response.headers.set("cache-control", "no-store")
    response.headers.set("referrer-policy", "no-referrer")
    return response
  }
})
