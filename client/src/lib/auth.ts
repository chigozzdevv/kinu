export interface Identity {
  uid: string
  email: string | null
  emailVerified: boolean
  displayName: string | null
}

export interface AuthenticationAdapter {
  observeIdentity(listener: (identity: Identity | null) => void): () => void
  signInWithEmail(email: string, password: string): Promise<void>
  signInWithSSO(): Promise<void>
  createEmailAccount(email: string, password: string): Promise<void>
  resetEmailPassword(email: string): Promise<void>
  identityToken(forceRefresh: boolean): Promise<string>
  signOut(): Promise<void>
}

let adapter: AuthenticationAdapter | null = null
export let authenticationConfigured = false

export function configureAuthentication(authentication: AuthenticationAdapter): void {
  if (adapter) throw new Error("Authentication is already configured")
  adapter = authentication
  authenticationConfigured = true
}

export class EmailVerificationRequiredError extends Error {
  constructor() {
    super("Verify your email before signing in")
    this.name = "EmailVerificationRequiredError"
  }
}

function configuredAuth(): AuthenticationAdapter {
  if (!adapter) throw new Error("Kinu authentication is not configured")
  return adapter
}

export function observeIdentity(listener: (identity: Identity | null) => void): () => void {
  if (!adapter) {
    listener(null)
    return () => undefined
  }
  return adapter.observeIdentity(listener)
}

export async function signInWithSSO(): Promise<void> {
  await configuredAuth().signInWithSSO()
}

export async function signInWithEmail(email: string, password: string): Promise<void> {
  await configuredAuth().signInWithEmail(email.trim(), password)
}

export async function createEmailAccount(email: string, password: string): Promise<void> {
  await configuredAuth().createEmailAccount(email.trim(), password)
}

export async function resetEmailPassword(email: string): Promise<void> {
  await configuredAuth().resetEmailPassword(email.trim())
}

export async function identityToken(forceRefresh = false): Promise<string> {
  const token = await configuredAuth().identityToken(forceRefresh)
  if (!token) throw new Error("Authentication required")
  return token
}

export async function signOutIdentity(): Promise<void> {
  if (adapter) await adapter.signOut()
}
