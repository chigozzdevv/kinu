import { X } from "lucide-react"
import { controlTriggers, type ControlValues } from "../lib/controls"
import { titleCase } from "../lib/format"
import type { Connection } from "../types"
import { Detail, DetailList } from "./detail"
import { Field, SelectControl } from "./workspace"

function sourceKey(connectionId: string, resource: string) {
  return JSON.stringify([connectionId, resource])
}

export function ControlsFields({ value, onChange, connections = [], onAddConnection }: { value: ControlValues; onChange: (value: ControlValues) => void; connections?: Connection[]; onAddConnection?: () => void }) {
  const sources = connections
    .filter((connection) => connection.status === "ready" && connection.roles.includes("incident"))
    .flatMap((connection) => connection.allowed_resources.map((resource) => ({ connection, resource })))
  const selected = new Set(value.exposureSources.map((source) => sourceKey(source.connection_id, source.resource)))
  return <div className="space-y-6">
    <fieldset>
      <legend className="mb-2 text-[10px] font-semibold text-[var(--ink-soft)]">Automatic rotation</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {controlTriggers.map(([trigger, label]) => <label key={trigger} className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-white px-4 py-3 text-[10px] font-medium">
          <input className="accent-[var(--ink)]" type="checkbox" checked={value.automaticTriggers.includes(trigger)} onChange={(event) => onChange({ ...value, automaticTriggers: event.target.checked ? [...value.automaticTriggers, trigger] : value.automaticTriggers.filter((item) => item !== trigger), ...(trigger === "verified-exposure" && !event.target.checked ? { exposureSources: [] } : {}) })} />
          {label}
        </label>)}
      </div>
    </fieldset>
    {value.automaticTriggers.includes("verified-exposure") && <Field label="Exposure sources"><div className="space-y-2"><SelectControl value="" onChange={(event) => {
      if (event.target.value === "__add__") { onAddConnection?.(); return }
      const [connectionId, resource] = JSON.parse(event.target.value) as [string, string]
      if (connectionId && resource) onChange({ ...value, exposureSources: [...value.exposureSources, { connection_id: connectionId, resource }] })
    }}><option value="">Select repository</option>{sources.filter(({ connection, resource }) => !selected.has(sourceKey(connection.id, resource))).map(({ connection, resource }) => <option key={`${connection.id}:${resource}`} value={sourceKey(connection.id, resource)}>{connection.display_name} · {resource}</option>)}{onAddConnection && <option value="__add__">Add connection…</option>}</SelectControl>{value.exposureSources.map((source) => <div key={`${source.connection_id}:${source.resource}`} className="flex items-center justify-between border-b border-[var(--border-soft)] px-1 py-2 text-[10px] last:border-b-0"><span>{connections.find((connection) => connection.id === source.connection_id)?.display_name ?? "Connection"} · {source.resource}</span><button type="button" className="focus-ring rounded-md p-1 text-[var(--ink-muted)] hover:text-[var(--ink)]" aria-label={`Remove ${source.resource}`} onClick={() => onChange({ ...value, exposureSources: value.exposureSources.filter((item) => item.connection_id !== source.connection_id || item.resource !== source.resource) })}><X className="size-3.5" /></button></div>)}</div></Field>}
    <div className="grid gap-4 sm:grid-cols-3">
      <Field label="Rotate before expiry"><SelectControl value={String(value.expiryDays)} onChange={(event) => onChange({ ...value, expiryDays: Number(event.target.value) })}><option value="3">3 days</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option></SelectControl></Field>
      <Field label="Verify before revoking"><SelectControl value={String(value.observationMinutes)} onChange={(event) => onChange({ ...value, observationMinutes: Number(event.target.value) })}><option value="15">15 minutes</option><option value="30">30 minutes</option><option value="60">1 hour</option><option value="240">4 hours</option></SelectControl></Field>
      <Field label="Human approval"><SelectControl value={value.requireRevokeApproval ? "required" : "automatic"} onChange={(event) => onChange({ ...value, requireRevokeApproval: event.target.value === "required" })}><option value="automatic">Not required</option><option value="required">Before revocation</option></SelectControl></Field>
    </div>
  </div>
}

export function ControlsSummary({ value, connections = [] }: { value: ControlValues; connections?: Connection[] }) {
  return <DetailList>
    <Detail label="Automatic rotation">{value.automaticTriggers.map(titleCase).join(", ")}</Detail>
    <Detail label="Expiry lead">{value.expiryDays} days</Detail>
    <Detail label="Verification period">{value.observationMinutes} minutes</Detail>
    <Detail label="Human approval">{value.requireRevokeApproval ? "Before revocation" : "Not required"}</Detail>
    {value.exposureSources.length > 0 && <Detail label="Exposure sources">{value.exposureSources.map((source) => `${connections.find((connection) => connection.id === source.connection_id)?.display_name ?? "Connection"} · ${source.resource}`).join(", ")}</Detail>}
  </DetailList>
}
