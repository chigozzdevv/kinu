import type { IntegrationKind } from "../components/integration"
import { clearCredentialReturn } from "./credential-return.ts"

export type BeforeAuthorization = (integration: IntegrationKind) => void

export function authorizeConnection(url: string, integration: IntegrationKind, before?: BeforeAuthorization) {
  clearCredentialReturn()
  before?.(integration)
  window.location.assign(url)
}

export function connectionCallbackIntegration(): IntegrationKind | null {
  const parameters = new URLSearchParams(window.location.search)
  if (parameters.has("hosting")) {
    const platform = parameters.get("platform")
    if (platform === "vercel" || platform === "railway" || platform === "render") return platform
  }
  if (parameters.has("gmail")) return "gmail"
  if (parameters.has("github") || parameters.has("installation_id")) return "github"
  if (parameters.has("code") && sessionStorage.getItem("kinu.github")) return "github"
  return null
}

export function dashboardLocation(): string {
  return connectionCallbackIntegration() ? `/dashboard/connections${window.location.search}` : "/dashboard"
}
