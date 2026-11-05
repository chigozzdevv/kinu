import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { IncidentKind } from "../lib/api/workflow"
import { useOrganisation } from "../lib/useorganisation"
import type { Incident, ManagedCredential } from "../types"
import { Button } from "./ui/button"
import { Modal } from "./ui/modal"
import { Field, SelectControl, formControl } from "./workspace"

const kinds: Array<[IncidentKind, string]> = [["exposure", "Key exposed"], ["expiry", "Near expiry"], ["drift", "Unexpected change"]]

export function IncidentCreate({ credentials, onClose, onCreated, onAddCredential, onConnectSource }: {
  credentials: ManagedCredential[]; onClose: () => void; onCreated: (incident: Incident) => void
  onAddCredential: () => void; onConnectSource: () => void
}) {
  const { active } = useOrganisation()
  const [credentialId, setCredentialId] = useState("")
  const [kind, setKind] = useState<IncidentKind>("exposure")
  const [details, setDetails] = useState("")
  const [simulate, setSimulate] = useState(false)
  const [sourceId, setSourceId] = useState("")
  const [recipient, setRecipient] = useState("")
  const [recipientEdited, setRecipientEdited] = useState(false)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  useEffect(() => { setRequestId(crypto.randomUUID()) }, [credentialId, kind, details, simulate, sourceId, recipient])
  const options = useQuery({ queryKey: ["incident-report-options", active.organisation.id, credentialId, kind],
    queryFn: () => api.getIncidentReportOptions(credentialId, kind), enabled: Boolean(credentialId) })
  useEffect(() => { if (!recipientEdited && options.data?.recipient) setRecipient(options.data.recipient) }, [options.data?.recipient, recipientEdited])
  const sources = options.data?.sources ?? []
  const linked = sources.filter((source) => source.linked)
  const source = sources.find((item) => item.id === sourceId) ?? (linked.length === 1 ? linked[0] : sources.length === 1 ? sources[0] : undefined)
  const create = useMutation({ mutationFn: () => api.createIncidentReport({
    credential_id: credentialId, kind, details: details.trim(), simulate,
    source_connection_id: simulate ? source?.id ?? null : null, recipient: simulate ? recipient.trim() : null,
  }, requestId), onSuccess: (result) => { if (!result.simulate && result.incident) onCreated(result.incident) } })
  const progress = useQuery({ queryKey: ["incident-report", active.organisation.id, create.data?.id],
    queryFn: () => api.getIncidentReportStatus(create.data!.id), enabled: create.data?.simulate === true,
    refetchInterval: (query) => ["completed", "failed"].includes(query.state.data?.state ?? "") ? false : 2_000 })
  const result = progress.data ?? create.data
  const awaiting = Boolean(result && !result.incident && result.state !== "failed")
  const delivery = result?.state === "queued" || result?.state === "sending" ? "Sending test alert…"
    : result?.state === "sent" ? "Waiting for the forwarded email…" : result?.state === "received" ? "Creating the test incident…" : "Test alert received"
  const canSubmit = Boolean(credentialId && !options.isFetching && !options.error
    && (!simulate || (source && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient.trim()))))
  const action = result ? result.incident ? <Button onClick={() => onCreated(result.incident!)}>Open incident</Button>
    : result.state === "failed" ? <Button onClick={() => { create.reset(); setRequestId(crypto.randomUUID()) }}>Back</Button> : <Button onClick={onClose}>Close</Button>
    : <Button disabled={!canSubmit || create.isPending} onClick={() => create.mutate()}>{create.isPending ? simulate ? "Queuing…" : "Creating…" : simulate ? "Send test alert" : "Create incident"}</Button>

  return <Modal isOpen onClose={() => { if (!create.isPending) onClose() }} title="Create incident" actions={action} cancelLabel={create.isPending || awaiting ? false : result ? "Close" : "Cancel"} showClose={!create.isPending}>
    {!credentials.length ? <div className="space-y-4"><p className="text-[12px] leading-6 text-[var(--ink-soft)]">{active.role === "administrator" ? "Add a credential to create an incident." : "Ask an administrator to add a credential."}</p>{active.role === "administrator" && <Button variant="secondary" onClick={onAddCredential}>Add credential</Button>}</div>
      : result ? <div className="space-y-4 text-[12px] leading-6">
        <p role="status" className={result.state === "failed" ? "text-[var(--red)]" : "font-medium"}>{result.state === "failed" ? result.message ?? "The test alert could not be completed." : delivery}</p>
        {result.recipient && <p className="break-all text-[var(--ink-soft)]">{result.recipient}</p>}
        {result.state === "sent" && <p className="text-[11px] text-[var(--ink-soft)]">Check that your mailbox forwards mail from {options.data?.email_sender} to your Kinu address.</p>}
        {progress.error && <p role="alert" className="text-[var(--red)]">{progress.error.message}</p>}
      </div> : <div className="space-y-4">
        <Field label="Credential"><SelectControl value={credentialId} disabled={create.isPending} onChange={(event) => { setCredentialId(event.target.value); setSourceId(""); create.reset() }}>
          <option value="">Select a credential</option>{credentials.map((credential) => <option key={credential.id} value={credential.id}>{credential.display_name}</option>)}
        </SelectControl></Field>
        <Field label="What happened?"><SelectControl value={kind} disabled={create.isPending} onChange={(event) => { setKind(event.target.value as IncidentKind); create.reset() }}>{kinds.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></Field>
        <Field label="Details (optional)"><textarea className={`${formControl} h-16 py-3`} maxLength={1024} value={details} disabled={create.isPending} onChange={(event) => setDetails(event.target.value)} /></Field>
        <div className="flex items-center justify-between gap-4 border-t border-[var(--border-soft)] pt-5">
          <label htmlFor="incident-simulate" className="text-[12px] font-medium">Simulate incident</label>
          <button id="incident-simulate" type="button" role="switch" aria-label="Simulate incident" aria-checked={simulate} disabled={create.isPending || !credentialId}
            className={`focus-ring relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors ${simulate ? "border-[var(--ink)] bg-[var(--ink)]" : "border-[var(--border)] bg-[var(--surface-soft)]"}`}
            onClick={() => { setSimulate(!simulate); create.reset() }}><span aria-hidden="true" className={`inline-block size-3.5 rounded-full bg-white shadow-sm transition-transform ${simulate ? "translate-x-[18px]" : "translate-x-0.5"}`} /></button>
        </div>
        {simulate && <div className="space-y-4">
          <p className="text-[11px] leading-5 text-[var(--ink-soft)]">A test alert can start a real rotation under this key’s controls.</p>
          {sources.length ? <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Incident source"><SelectControl value={source?.id ?? ""} disabled={create.isPending || sources.length === 1} onChange={(event) => setSourceId(event.target.value)}><option value="">Select an Email connection</option>{sources.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</SelectControl></Field>
              <Field label="Send test to"><input className={formControl} type="email" autoComplete="email" value={recipient} disabled={create.isPending} onChange={(event) => { setRecipient(event.target.value); setRecipientEdited(true) }} /></Field>
            </div>
            <details className="break-words border-t border-[var(--border-soft)] pt-4 text-[11px] leading-5">
              <summary className="focus-ring w-fit cursor-pointer rounded font-medium">Email preview</summary>
              <p className="mt-3 font-medium">{options.data?.subject}</p><p className="mt-2 whitespace-pre-line text-[var(--ink-soft)]">{options.data?.preview}</p>
            </details>
          </> : !options.isFetching && <div className="space-y-3"><p className="text-[11px] leading-5 text-[var(--ink-soft)]">Connect an Email source to send a test alert.</p><Button variant="secondary" size="sm" onClick={onConnectSource}>Connect source</Button></div>}
        </div>}
        {(create.error || options.error) && <p role="alert" className="text-[11px] leading-5 text-[var(--red)]">{(create.error ?? options.error)?.message}</p>}
      </div>}
  </Modal>
}
