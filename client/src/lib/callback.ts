import type { IntegrationKind } from "../components/integration"

export function connectionCallbackIntegration(): IntegrationKind | null {
  const parameters = new URLSearchParams(window.location.search)
  if (parameters.has("github") || parameters.has("installation_id")) return "github"
  if (parameters.has("code") && sessionStorage.getItem("kinu.github")) return "github"
  return null
}

export function dashboardLocation(): string {
  return connectionCallbackIntegration() ? `/dashboard/connections${window.location.search}` : "/dashboard"
}
