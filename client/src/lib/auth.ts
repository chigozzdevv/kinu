import { apiCredentials, apiUrl } from "./api-origin.ts"

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

export const dashboardReturnKey = "kinu.dashboard-return"
export const sessionExpiredEvent = "kinu:session-expired"
let cachedSession: BrowserSession | null = null
let redirecting = false

export class SessionExpiredError extends Error {
  constructor() {
    super("Sign in to continue")
    this.name = "SessionExpiredError"
  }
}

export function redirectToSignIn(expired = true): void {
  clearSession()
  if (redirecting || window.location.pathname === "/auth") return
  redirecting = true
  const { pathname, search } = window.location
  if (pathname === "/dashboard" || pathname.startsWith("/dashboard/")) {
    sessionStorage.setItem(dashboardReturnKey, pathname + search)
  }
  window.dispatchEvent(new Event(sessionExpiredEvent))
  window.location.replace(expired ? "/auth?expired=1" : "/auth")
}

export function watchSession(expiresAt: string): () => void {
  const expiry = Date.parse(expiresAt)
  const check = () => { if (Date.now() >= expiry) redirectToSignIn() }
  const timer = window.setTimeout(check, Math.max(0, expiry - Date.now()))
  window.addEventListener("focus", check)
  document.addEventListener("visibilitychange", check)
  return () => {
    window.clearTimeout(timer)
    window.removeEventListener("focus", check)
    document.removeEventListener("visibilitychange", check)
  }
}

export function clearSession(): void {
  cachedSession = null
}

export async function readSession(): Promise<BrowserSession | null> {
  const response = await fetch(apiUrl("/auth/session"), { credentials: apiCredentials, cache: "no-store" })
  if (response.status === 401) {
    clearSession()
    return null
  }
  if (!response.ok) throw new Error("Your sign-in could not be checked. Try again.")
  cachedSession = await response.json() as BrowserSession
  return cachedSession
}

export async function readAuthentication(): Promise<Authentication> {
  const response = await fetch(apiUrl("/auth/status"), { credentials: apiCredentials, cache: "no-store" })
  if (!response.ok) throw new Error("Sign-in is temporarily unavailable. Try again.")
  const status = await response.json() as { configured: boolean }
  return { configured: status.configured, session: status.configured ? await readSession() : null }
}

export interface AuthResult {
  next: "complete" | "signin" | "verify" | "forgot" | "reset"
  return_to?: string
}

export async function submitAuth(action: "signin" | "signup" | "verify" | "resend" | "forgot" | "reset", body: Record<string, string>): Promise<AuthResult> {
  const form = await fetch(apiUrl("/auth/form"), { credentials: apiCredentials, cache: "no-store" })
  if (!form.ok) throw new Error("Sign-in is temporarily unavailable. Please try again.")
  const state = await form.json() as { csrf_token: string }
  const response = await fetch(apiUrl(`/auth/${action}`), {
    method: "POST", credentials: apiCredentials, cache: "no-store",
    headers: { "content-type": "application/json", "x-csrf-token": state.csrf_token },
    body: JSON.stringify(body),
  })
  const result = await response.json().catch(() => null) as (AuthResult & { message?: string }) | null
  if (!response.ok || !result) throw new Error(result?.message ?? "Sign-in could not be completed. Please try again.")
  return result
}

export async function sessionHeaders(method = "GET"): Promise<Headers> {
  const headers = new Headers()
  if (redirecting || cachedSession && Date.parse(cachedSession.expires_at) <= Date.now()) {
    redirectToSignIn()
    throw new SessionExpiredError()
  }
  if (["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) return headers
  const current = cachedSession && Date.parse(cachedSession.expires_at) > Date.now()
    ? cachedSession : await readSession()
  if (!current) {
    redirectToSignIn()
    throw new SessionExpiredError()
  }
  headers.set("X-CSRF-Token", current.csrf_token)
  return headers
}

export async function sessionFetch(path: string, options?: RequestInit): Promise<Response> {
  const headers = new Headers(options?.headers)
  const authentication = await sessionHeaders(options?.method)
  authentication.forEach((value, name) => headers.set(name, value))
  if (options?.body && !headers.has("content-type")) headers.set("content-type", "application/json")
  const response = await fetch(apiUrl(path), { ...options, credentials: apiCredentials, headers })
  if (response.status === 401) {
    redirectToSignIn()
    throw new SessionExpiredError()
  }
  return response
}

export async function signOutIdentity(): Promise<string> {
  const current = cachedSession ?? await readSession()
  const response = await fetch(apiUrl("/auth/logout"), {
    method: "POST",
    credentials: apiCredentials,
    headers: current ? { "X-CSRF-Token": current.csrf_token } : {},
  })
  if (!response.ok) throw new Error("Sign-out could not be completed. Try again.")
  const result = await response.json() as { logout_url: string }
  clearSession()
  return result.logout_url
}
