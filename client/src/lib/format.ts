import type { ConnectionStatus } from "../types"

export function titleCase(value: string): string {
  const terms: Record<string, string> = {
    api: "API",
    cli: "CLI",
    github: "GitHub",
    http: "HTTP",
    id: "ID",
    mfa: "MFA",
    oauth: "OAuth",
    saas: "SaaS",
    url: "URL",
  }
  return value.replace(/[._-]+/g, " ").split(/\s+/).filter(Boolean).map((word) => terms[word.toLowerCase()] ?? `${word[0]?.toUpperCase() ?? ""}${word.slice(1)}`).join(" ")
}

export function formatDate(value: string, withTime = false): string {
  return new Intl.DateTimeFormat("en", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(value))
}

export function shortId(value: string | null, size = 14): string {
  if (!value) return "—"
  return value.length > size ? `${value.slice(0, size)}…` : value
}

export function providerName(value: string): string {
  const names: Record<string, string> = {
    "internal-vendor": "Internal Vendor",
    github: "GitHub",
    netsuite: "NetSuite",
    segment: "Segment",
    sendgrid: "SendGrid",
    snowflake: "Snowflake",
    stripe: "Stripe",
  }
  return names[value] ?? titleCase(value)
}

export function connectionStatus(status: ConnectionStatus | undefined): { label: string; variant: "healthy" | "warning" | "neutral" } {
  if (status === "ready") return { label: "Ready", variant: "healthy" }
  if (status === "setup-required") return { label: "Setup required", variant: "warning" }
  if (status === "reauthentication-required" || status === "degraded") return { label: "Action required", variant: "warning" }
  return { label: status === "disabled" ? "Disabled" : "Unknown", variant: "neutral" }
}

export function connectionAction(status: ConnectionStatus): string {
  if (status === "setup-required" || status === "reauthentication-required") return "Set up"
  if (status === "degraded") return "Review connection"
  return "View details"
}
