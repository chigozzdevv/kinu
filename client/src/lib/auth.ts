export interface Identity {
  uid: string
  email: string
  emailVerified: boolean
  displayName: string | null
}

export interface BrowserSession {
  identity: Identity
  csrf_token: string
  expires_at: string
}

export interface Authentication {
  configured: boolean
  session: BrowserSession | null
}

let cachedSession: BrowserSession | null = null

export function clearSession(): void {
  cachedSession = null
}

export async function readSession(): Promise<BrowserSession | null> {
  const response = await fetch("/auth/session", { credentials: "same-origin", cache: "no-store" })
  if (response.status === 401) {
    clearSession()
    return null
  }
  if (!response.ok) throw new Error("Your sign-in could not be checked. Try again.")
  cachedSession = await response.json() as BrowserSession
  return cachedSession
}

export async function readAuthentication(): Promise<Authentication> {
  const response = await fetch("/auth/status", { credentials: "same-origin", cache: "no-store" })
  if (!response.ok) throw new Error("Sign-in is temporarily unavailable. Try again.")
  const status = await response.json() as { configured: boolean }
  return { configured: status.configured, session: status.configured ? await readSession() : null }
}

export function signIn(returnTo = "/dashboard"): void {
  window.location.assign(`/auth/login?${new URLSearchParams({ return_to: returnTo })}`)
}

export async function sessionHeaders(method = "GET"): Promise<Headers> {
  const headers = new Headers()
  if (["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) return headers
  const current = cachedSession && Date.parse(cachedSession.expires_at) > Date.now()
    ? cachedSession : await readSession()
  if (!current) {
    window.location.assign("/auth")
    throw new Error("Sign in to continue")
  }
  headers.set("X-CSRF-Token", current.csrf_token)
  return headers
}

export async function signOutIdentity(): Promise<string> {
  const current = cachedSession ?? await readSession()
  const response = await fetch("/auth/logout", {
    method: "POST",
    credentials: "same-origin",
    headers: current ? { "X-CSRF-Token": current.csrf_token } : {},
  })
  if (!response.ok) throw new Error("Sign-out could not be completed. Try again.")
  const result = await response.json() as { logout_url: string }
  clearSession()
  return result.logout_url
}
