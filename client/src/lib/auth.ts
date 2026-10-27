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

export interface AuthResult {
  next: "complete" | "signin" | "verify" | "forgot" | "reset"
  return_to?: string
}

export async function submitAuth(action: "signin" | "signup" | "verify" | "resend" | "forgot" | "reset", body: Record<string, string>): Promise<AuthResult> {
  const form = await fetch("/auth/form", { credentials: "same-origin", cache: "no-store" })
  if (!form.ok) throw new Error("Sign-in is temporarily unavailable. Please try again.")
  const state = await form.json() as { csrf_token: string }
  const response = await fetch(`/auth/${action}`, {
    method: "POST", credentials: "same-origin", cache: "no-store",
    headers: { "content-type": "application/json", "x-csrf-token": state.csrf_token },
    body: JSON.stringify(body),
  })
  const result = await response.json().catch(() => null) as (AuthResult & { message?: string }) | null
  if (!response.ok || !result) throw new Error(result?.message ?? "Sign-in could not be completed. Please try again.")
  return result
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
