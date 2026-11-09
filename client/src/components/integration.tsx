import { ChevronRight, Pencil } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { providerName } from "../lib/format"
import { PlatformIcon } from "./platform-icon"

export type IntegrationKind = "email" | "github" | "gmail" | "aws" | "custom-api" | "computer-use" | "vercel" | "railway" | "render"

type Integration = {
  id: IntegrationKind
  description: string
  action: string
}

const integrations: Integration[] = [
  { id: "email", description: "Forwarded email alerts", action: "Connect" },
  { id: "gmail", description: "Incident emails", action: "Connect" },
  { id: "github", description: "Secret scanning", action: "Connect" },
  { id: "vercel", description: "Projects and variables", action: "Connect" },
  { id: "railway", description: "Projects and variables", action: "Connect" },
  { id: "render", description: "Services and variables", action: "Connect" },
  { id: "aws", description: "Secrets and workloads", action: "Connect" },
  { id: "custom-api", description: "Provider credential API", action: "Configure" },
  { id: "computer-use", description: "Provider actions through a Playbook", action: "Set up" },
]

function IntegrationLogo({ kind }: { kind: IntegrationKind }) {
  return <span className="grid size-14 place-items-center rounded-xl border border-[var(--border-soft)] bg-white" aria-hidden="true">
    <PlatformIcon platform={kind} size={32} />
  </span>
}

function EditableConnectionName({ value, onChange, disabled }: { value: string; onChange: (name: string) => void; disabled: boolean }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const input = useRef<HTMLInputElement>(null)
  const cancelled = useRef(false)

  useEffect(() => {
    if (editing) { input.current?.focus(); input.current?.select() }
  }, [editing])

  function finish() {
    const next = draft.trim() || value
    if (!cancelled.current && next !== value) onChange(next)
    cancelled.current = false
    setEditing(false)
  }

  if (editing) return <input ref={input} aria-label="Connection name" value={draft} maxLength={100} disabled={disabled}
    autoComplete="off" spellCheck={false}
    className="max-w-full border-0 border-b border-[var(--ink-muted)] bg-transparent px-1 py-0.5 text-center text-[13px] font-semibold text-[var(--ink)] outline-none"
    style={{ width: `${Math.min(Math.max(draft.length + 2, 8), 30)}ch` }}
    onChange={(event) => setDraft(event.target.value)} onBlur={finish}
    onKeyDown={(event) => {
      if (event.key === "Enter" || event.key === "Escape") {
        event.preventDefault()
        cancelled.current = event.key === "Escape"
        event.currentTarget.blur()
      }
    }} />

  return <button type="button" aria-label={`Rename ${value} connection`} title="Rename connection" disabled={disabled}
    className="focus-ring relative max-w-full rounded px-5 py-0.5 text-[13px] font-semibold text-[var(--ink)] disabled:cursor-default disabled:opacity-60"
    onClick={() => { setDraft(value); cancelled.current = false; setEditing(true) }}>
    <span className="[overflow-wrap:anywhere]">{value}</span>
    <Pencil className="absolute right-0 top-1/2 size-3 -translate-y-1/2 text-[var(--ink-muted)]" aria-hidden="true" />
  </button>
}

export function IntegrationMark({ kind, name, onNameChange, disabled = false }: {
  kind: IntegrationKind; name?: string; onNameChange?: (name: string) => void; disabled?: boolean
}) {
  const label = name ?? providerName(kind)
  return <div className="flex max-w-full flex-col items-center gap-3">
    <IntegrationLogo kind={kind} />
    {onNameChange ? <EditableConnectionName value={label} onChange={onNameChange} disabled={disabled} />
      : <span className="text-[13px] font-semibold text-[var(--ink)]">{label}</span>}
  </div>
}

export function IntegrationGrid({
  visible,
  connected,
  available,
  onSelect,
  unconfigured = {},
}: {
  visible: IntegrationKind[]
  connected: Partial<Record<IntegrationKind, boolean>>
  available: Record<IntegrationKind, boolean>
  unconfigured?: Partial<Record<IntegrationKind, boolean>>
  onSelect: (integration: IntegrationKind) => void
}) {
  return <div className="grid gap-4 sm:grid-cols-2">
    {integrations.filter((integration) => visible.includes(integration.id) && (available[integration.id] || unconfigured[integration.id])).map((integration) => {
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
          {!available[integration.id] ? "Not configured" : isConnected && ["github", "email"].includes(integration.id) ? "Manage" : isConnected ? "Add another" : integration.action}
          <ChevronRight className="content-chevron ml-auto size-3.5" />
        </span>
      </button>
    })}
  </div>
}
