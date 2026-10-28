import { Icon } from "@iconify/react"
import githubIcon from "@iconify-icons/logos/github-icon.js"
import aws from "@iconify-icons/logos/aws.js"
import gmail from "@iconify-icons/logos/google-gmail.js"
import { Braces, ChevronRight, MonitorCog } from "lucide-react"
import type { ElementType } from "react"

export type IntegrationKind = "github" | "gmail" | "aws" | "custom-api" | "computer-use"

type Integration = {
  id: IntegrationKind
  name: string
  description: string
  action: string
  logo?: typeof githubIcon
  icon?: ElementType
}

const integrations: Integration[] = [
  { id: "gmail", name: "Gmail", description: "Incident emails", action: "Connect", logo: gmail },
  { id: "github", name: "GitHub", description: "Secret scanning", action: "Connect", logo: githubIcon },
  { id: "aws", name: "AWS", description: "Secrets and workloads", action: "Connect", logo: aws },
  { id: "custom-api", name: "Custom API", description: "Provider credential API", action: "Configure", icon: Braces },
  { id: "computer-use", name: "Computer Use", description: "Provider actions through a Playbook", action: "Set up", icon: MonitorCog },
]

function IntegrationLogo({ integration }: { integration: Integration }) {
  const Fallback = integration.icon
  return <span className="grid size-14 place-items-center rounded-xl border border-[var(--border-soft)] bg-white" aria-hidden="true">
    {integration.logo ? <Icon icon={integration.logo} className="size-8" /> : Fallback ? <Fallback className="size-6 text-[var(--ink-soft)]" strokeWidth={1.7} /> : null}
  </span>
}

export function IntegrationMark({ kind }: { kind: IntegrationKind }) {
  const integration = integrations.find((item) => item.id === kind)
  if (!integration) return null

  return <div className="flex flex-col items-center gap-3">
    <IntegrationLogo integration={integration} />
    <span className="text-[13px] font-semibold text-[var(--ink)]">{integration.name}</span>
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
        className="focus-ring group disabled:cursor-not-allowed disabled:opacity-60 flex min-h-48 flex-col rounded-2xl border border-[var(--border)] bg-white p-5 text-left transition hover:border-[var(--ink-muted)]"
        onClick={() => onSelect(integration.id)}
      >
        <IntegrationLogo integration={integration} />
        <span className="mt-7 text-[13px] font-semibold text-[var(--ink)]">{integration.name}</span>
        <span className="mt-1 text-[10px] text-[var(--ink-muted)]">{integration.description}</span>
        <span className="mt-auto flex items-center pt-5 text-[10px] font-semibold text-[var(--ink-soft)] group-hover:text-[var(--ink)]">
          {!available[integration.id] ? "Unavailable" : isConnected && integration.id === "github" ? "Disconnect" : isConnected ? "Add another" : integration.action}
          <ChevronRight className="ml-auto size-3.5" />
        </span>
      </button>
    })}
  </div>
}
