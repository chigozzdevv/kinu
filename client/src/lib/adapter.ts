import type { HttpOperation, HttpProviderApi } from "../types"

function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`)
  return value as Record<string, unknown>
}

function string(value: unknown, label: string, optional = false): string | null {
  if (optional && (value === null || value === undefined || value === "")) return null
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label} is required`)
  return value
}

function operation(value: unknown, label: string): HttpOperation {
  const raw = object(value, label)
  const method = string(raw.method, `${label} method`) as HttpOperation["method"]
  if (!(["GET", "POST", "PUT", "PATCH", "DELETE"] as string[]).includes(method)) throw new Error(`${label} method is invalid`)
  const path = string(raw.path, `${label} path`)!
  if (!path.startsWith("/") || path.includes("://") || /[?#]/.test(path)) throw new Error(`${label} path must be origin-relative`)
  const statuses = raw.success_statuses
  if (!Array.isArray(statuses) || !statuses.length || statuses.some((status) => !Number.isInteger(status) || status < 100 || status > 599)) throw new Error(`${label} success statuses are invalid`)
  return {
    method,
    path,
    success_statuses: statuses as number[],
    query: object(raw.query ?? {}, `${label} query`) as Record<string, string>,
    body: object(raw.body ?? {}, `${label} body`),
    list_items: string(raw.list_items, `${label} list items`, true),
    provider_id_field: string(raw.provider_id_field, `${label} credential ID`, true),
    secret_field: string(raw.secret_field, `${label} secret field`, true),
    name_field: string(raw.name_field, `${label} name field`, true),
    metadata_fields: object(raw.metadata_fields ?? {}, `${label} metadata fields`) as Record<string, string>,
  }
}

function auth(value: unknown, label: string): HttpProviderApi["auth"] {
  const raw = object(value, label)
  const scheme = string(raw.scheme, `${label} scheme`) as HttpProviderApi["auth"]["scheme"]
  if (!(["bearer", "header", "basic"] as string[]).includes(scheme)) throw new Error(`${label} scheme is invalid`)
  return { scheme, header: string(raw.header, `${label} header`)!, prefix: string(raw.prefix, `${label} prefix`, true) }
}

export function parseProviderAdapter(source: string): HttpProviderApi {
  let parsed: unknown
  try {
    parsed = JSON.parse(source)
  } catch {
    throw new Error("API definition must be valid JSON")
  }
  const raw = object(parsed, "API definition")
  const baseUrl = string(raw.base_url, "API base URL")!
  let url: URL
  try {
    url = new URL(baseUrl)
  } catch {
    throw new Error("API base URL is invalid")
  }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) throw new Error("API base URL must be an HTTPS origin")
  const listCredentials = operation(raw.list_credentials, "List operation")
  const createCredential = operation(raw.create_credential, "Create operation")
  const revokeCredential = operation(raw.revoke_credential, "Revoke operation")
  if (!listCredentials.provider_id_field) throw new Error("List operation must declare the credential ID field")
  if (!createCredential.provider_id_field || !createCredential.secret_field) throw new Error("Create operation must declare credential ID and secret fields")
  if (!revokeCredential.path.includes("{provider_id}")) throw new Error("Revoke operation path must contain {provider_id}")
  const testCredential = raw.test_credential == null ? null : operation(raw.test_credential, "Credential test operation")
  const credentialAuth = raw.credential_auth == null ? null : auth(raw.credential_auth, "Credential authentication")
  if (!testCredential || !credentialAuth || !testCredential.provider_id_field) throw new Error("API definition must declare a credential identity test")
  return {
    base_url: url.origin + (url.pathname === "/" ? "" : url.pathname.replace(/\/$/, "")),
    auth: auth(raw.auth, "Management authentication"),
    list_credentials: listCredentials,
    create_credential: createCredential,
    revoke_credential: revokeCredential,
    test_credential: testCredential,
    credential_auth: credentialAuth,
  }
}
