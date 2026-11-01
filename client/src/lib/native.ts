import { sessionFetch } from "./auth"

export interface BuilderStatus { connected: boolean, model: string, verified_at: number | null, models: string[], usage: { calls: number; limit: number } }

export async function nativeRequest<T>(workspace: string, path: string, body?: unknown): Promise<T> {
  const method = body === undefined ? "GET" : "POST"
  const headers = new Headers()
  headers.set("x-kinu-workspace", workspace)
  if (body !== undefined) headers.set("content-type", "application/json")
  const response = await sessionFetch(`/_agent-native/kinu/${path}`, {
    method, headers, credentials: "same-origin", cache: "no-store",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const value = await response.json().catch(() => null) as T & { error?: string } | null
  if (!response.ok || value === null) throw new Error(value?.error ?? "The agent service is unavailable. Try again.")
  return value
}
