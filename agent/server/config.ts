export interface Config {
  apiUrl: string
  publicUrl: string
  cookieName: string
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  if (env.KINU_MONGO_URL || env.KINU_VAULT_URL || env.KINU_VAULT_KEY || env.KINU_RESEND_KEY || env.KINU_SETUP_TOKEN || env.KINU_SETUP_TOKEN_FILE) {
    throw new Error("Private API storage credentials cannot enter the agent service")
  }
  if (env.KINU_GMAIL_SECRET_ARN || env.KINU_GMAIL_TABLE) {
    throw new Error("Private mailbox storage cannot enter the agent service")
  }
  if (["1", "true"].includes(env.AUTH_DISABLED?.toLowerCase() ?? "") ||
      env.AUTH_MODE === "local" || env.ACCESS_TOKEN || env.ACCESS_TOKENS) {
    throw new Error("Kinu requires its own authenticated session")
  }
  const api = new URL(env.KINU_API_URL ?? "http://127.0.0.1:8000")
  const publicUrl = new URL(env.KINU_PUBLIC_URL ?? "http://localhost:5173")
  for (const url of [api, publicUrl]) {
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash) {
      throw new Error("Kinu service URLs must be HTTP origins without credentials")
    }
  }
  if (publicUrl.protocol !== "https:" &&
      (env.KINU_ENVIRONMENT === "production" || publicUrl.hostname !== "localhost")) {
    throw new Error("Kinu requires HTTPS outside development localhost")
  }
  return {
    apiUrl: api.origin,
    publicUrl: publicUrl.origin,
    cookieName: publicUrl.protocol === "https:" ? "__Host-kinu" : "kinu.session",
  }
}
