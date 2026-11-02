import { ChevronRight } from "lucide-react"
import { providerName } from "../lib/format"
import { PlatformIcon } from "./platform-icon"

export type IntegrationKind = "github" | "gmail" | "aws" | "custom-api" | "computer-use" | "vercel" | "railway" | "render" | "resend"

type Integration = {
  id: IntegrationKind
  description: string
  action: string
}

const integrations: Integration[] = [
  { id: "gmail", description: "Incident emails", action: "Connect" },
  { id: "github", description: "Secret scanning", action: "Connect" },
  { id: "vercel", description: "Projects and variables", action: "Connect" },
  { id: "railway", description: "Projects and variables", action: "Connect" },
  { id: "render", description: "Services and variables", action: "Connect" },
  { id: "resend", description: "Sending keys", action: "Connect" },
  { id: "aws", description: "Secrets and workloads", action: "Connect" },
  { id: "custom-api", description: "Provider credential API", action: "Configure" },
  { id: "computer-use", description: "Provider actions through a Playbook", action: "Set up" },
]

function IntegrationLogo({ kind }: { kind: IntegrationKind }) {
  return <span className="grid size-14 place-items-center rounded-xl border border-[var(--border-soft)] bg-white" aria-hidden="true">
    <PlatformIcon platform={kind} size={32} />
  </span>
}

export function IntegrationMark({ kind }: { kind: IntegrationKind }) {
  return <div className="flex flex-col items-center gap-3">
    <IntegrationLogo kind={kind} />
    <span className="text-[13px] font-semibold text-[var(--ink)]">{providerName(kind)}</span>
  </div>
}

export function IntegrationGrid({
  visible,
  connected,
  available,
  onSelect,
}: {
  visible: IntegrationKind[]
  connected: Partial<Record<IntegrationKind, boolean>>
  available: Record<IntegrationKind, boolean>
  onSelect: (integration: IntegrationKind) => void
}) {
  return <div className="grid gap-4 sm:grid-cols-2">
    {integrations.filter((integration) => visible.includes(integration.id) && available[integration.id]).map((integration) => {
      const isConnected = connected[integration.id] === true
      return <button
        key={integration.id}
        type="button"
        disabled={!available[integration.id]}
        className="focus-ring content-hover group disabled:cursor-not-allowed disabled:opacity-60 flex min-h-48 flex-col rounded-2xl border border-[var(--border)] bg-white p-5 text-left"
        onClick={() => onSelect(integration.id)}
      >
        <IntegrationLogo kind={integration.id} />
        <span className="mt-7 text-[13px] font-semibold text-[var(--ink)]">{providerName(integration.id)}</span>
        <span className="mt-1 text-[10px] text-[var(--ink-muted)]">{integration.description}</span>
        <span className="mt-auto flex items-center pt-5 text-[10px] font-semibold text-[var(--ink-soft)] group-hover:text-[var(--ink)]">
          {!available[integration.id] ? "Unavailable" : isConnected && integration.id === "github" ? "Disconnect" : isConnected ? "Add another" : integration.action}
          <ChevronRight className="content-chevron ml-auto size-3.5" />
        </span>
      </button>
    })}
  </div>
}
