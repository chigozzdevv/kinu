import type { Identifier } from "../../types"
import { sessionFetch } from "../auth"

let organisationId = ""

export function setActiveOrganisationId(value: Identifier) {
  if (!/^[a-z][a-z0-9_-]{2,127}$/.test(value)) throw new Error("Organisation ID is invalid")
  organisationId = value
}

export function activeOrganisationId(): Identifier {
  if (!organisationId) throw new Error("Select an organisation first")
  return organisationId
}


export function workspacePath(): string { return `/v1/organisations/${activeOrganisationId()}` }

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(
    message: string,
    status: number,
    code: string,
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await sessionFetch(path, options)
    if (!response.ok) {
      const problem = (await response.json().catch(() => null)) as { code?: string; message?: string } | null
      throw new ApiError(
        problem?.message ?? `Kinu API request failed (${response.status})`,
        response.status,
        problem?.code ?? "request-failed",
      )
    }
    return response.json() as Promise<T>
  }

export async function requestBlob(path: string): Promise<Blob> {
    const response = await sessionFetch(path, {
      headers: { Accept: "image/png" },
    })
    if (!response.ok) {
      const problem = (await response.json().catch(() => null)) as { code?: string; message?: string } | null
      throw new ApiError(
        problem?.message ?? `Kinu API request failed (${response.status})`,
        response.status,
        problem?.code ?? "request-failed",
      )
    }
    return response.blob()
  }

export async function archive<T>(resourcePath: string, expectedRevision: number): Promise<T> {
    return request(`${resourcePath}/archive`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, cascade: true }),
    })
  }
