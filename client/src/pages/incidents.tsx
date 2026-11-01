import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, ChevronRight } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailPage } from "../components/detail"
import { PageHeader } from "../components/header"
import { DemoAlert } from "../components/demo"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, SelectControl } from "../components/workspace"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"
import { affectedResource, incidentReason } from "../lib/incident"
import type { Incident } from "../types"

const dismissalReasons = [
  "False positive",
  "Duplicate incident",
  "Test credential",
  "Credential is not managed by Kinu",
]

function severityVariant(severity: Incident["severity"]) {
  if (severity === "critical" || severity === "high") return "danger" as const
  if (severity === "medium") return "warning" as const
  return "neutral" as const
}

function statusVariant(status: Incident["status"]) {
  if (status === "resolved" || status === "contained") return "healthy" as const
  if (status === "action-required") return "warning" as const
  return "active" as const
}

export function IncidentsPage({ initialIncidentId = "", onSelectIncident, onNavigateRotation }: { initialIncidentId?: string; onSelectIncident: (incidentId: string) => void; onNavigateRotation: (runId: string) => void }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("all")
  const [selectedId, setSelectedId] = useState(initialIncidentId)
  const [selectedCandidateId, setSelectedCandidateId] = useState("")
  const [dismissing, setDismissing] = useState(false)
  const [dismissalReason, setDismissalReason] = useState(dismissalReasons[0])
  const [incidents, graph] = useQueries({ queries: [
    { queryKey: ["incidents"], queryFn: () => api.getIncidents(), refetchInterval: 10_000 },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (incidents.data ?? []).filter((item) => {
      const open = !["resolved", "dismissed"].includes(item.status)
      const matchesStatus = status === "all" || (status === "open" ? open : item.status === status)
      const haystack = `${item.source} ${item.source_event_id} ${incidentReason(item.kind)} ${item.resource.repository} ${item.resource.service} ${affectedResource(item, graph.data).name}`.toLowerCase()
      return matchesStatus && (!term || haystack.includes(term))
    })
  }, [incidents.data, graph.data, search, status])

  const selected = incidents.data?.find((incident) => incident.id === selectedId)
  const credential = (id: string | null) => graph.data?.credentials.find((item) => item.id === id)

  useEffect(() => {
    if (!selected || selectedCandidateId) return
    const candidateId = selected.credential_id ?? (selected.candidates.length === 1 ? selected.candidates[0].credential_id : "")
    if (candidateId) setSelectedCandidateId(candidateId)
  }, [selected, selectedCandidateId])

  const resolve = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("Incident is unavailable")
      const credentialId = selected.credential_id ?? selectedCandidateId
      if (!credentialId) throw new Error("Select the affected credential")
      const confirmed = selected.credential_id
        ? selected
        : await api.confirmIncident(selected.id, selected.revision, credentialId)
      const affected = credential(confirmed.credential_id)
      if (!affected) throw new Error("The confirmed credential is unavailable")
      const urgency = selected.severity === "critical" ? "emergency" : selected.severity === "high" ? "urgent" : "routine"
      return api.startIncidentRotation(
        confirmed.id,
        affected.control_version,
        `${titleCase(selected.kind)} reported by ${titleCase(selected.source)}`,
        urgency,
      )
    },
    onSuccess: async ({ incident, run }) => {
      queryClient.setQueryData<Incident[]>(["incidents"], (current) => current?.map((item) => item.id === incident.id ? incident : item))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["incidents"] }),
        queryClient.invalidateQueries({ queryKey: ["rotations"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ])
      onNavigateRotation(run.id)
    },
  })

  const dismiss = useMutation({
    mutationFn: () => api.dismissIncident(selected!.id, selected!.revision, dismissalReason),
    onSuccess: async (incident) => {
      queryClient.setQueryData<Incident[]>(["incidents"], (current) => current?.map((item) => item.id === incident.id ? incident : item))
      setDismissing(false)
      setSelectedId("")
      onSelectIncident("")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["incidents"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ])
    },
  })

  const openIncident = (incident: Incident) => {
    setSelectedId(incident.id)
    onSelectIncident(incident.id)
    setSelectedCandidateId(incident.credential_id ?? (incident.candidates.length === 1 ? incident.candidates[0].credential_id : ""))
    resolve.reset()
    dismiss.reset()
  }

  if ([incidents, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [incidents, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  if (selected) {
    const canResolve = selected.status === "action-required"
    const canDismiss = ["new", "correlating", "action-required"].includes(selected.status)
    const requiresCredentialChoice = canResolve && !selected.credential_id
    const linkedRunId = selected.resolution_run_id ?? selected.run_id
    const actions = linkedRunId
      ? <Button onClick={() => onNavigateRotation(linkedRunId)}>Open rotation <ArrowUpRight className="size-3.5" /></Button>
      : canResolve || canDismiss
        ? <>{canDismiss && <Button variant="secondary" onClick={() => setDismissing(true)}>Dismiss</Button>}{canResolve && <Button disabled={resolve.isPending || !selectedCandidateId} onClick={() => resolve.mutate()}>{resolve.isPending ? "Starting…" : selected.credential_id ? "Start rotation" : "Confirm and rotate"}</Button>}</>
        : undefined

    return <DetailPage>
      <PageHeader eyebrow="Operations / Incidents" title={titleCase(selected.source)} onBack={() => { setSelectedId(""); onSelectIncident("") }} actions={actions} />
      {(resolve.error || dismiss.error) && <div role="alert" className="mb-5 text-[10px] text-[var(--red)]">{(resolve.error ?? dismiss.error)?.message}</div>}
      <DetailCard>
        <DetailList><Detail label="Reason">{incidentReason(selected.kind)}</Detail><Detail label="Affected resource">{affectedResource(selected, graph.data).name}</Detail>{selected.credential_id && <Detail label="Credential">{credential(selected.credential_id)?.display_name ?? "Credential"}</Detail>}<Detail label="Status">{titleCase(selected.status)}</Detail><Detail label="Reference">{selected.source_event_id}</Detail>{selected.resolution_run_id && selected.run_id && selected.resolution_run_id !== selected.run_id && <Detail label="Previous attempt"><button type="button" className="focus-ring rounded hover:underline" onClick={() => onNavigateRotation(selected.run_id!)}>View rotation</button></Detail>}{selected.resource.repository && <Detail label="Repository">{selected.resource.repository}</Detail>}{selected.resource.project && <Detail label="Project">{selected.resource.project}</Detail>}{selected.resource.service && <Detail label="Service">{selected.resource.service}</Detail>}<Detail label="Observed">{formatDate(selected.created_at, true)}</Detail></DetailList>
        {requiresCredentialChoice && <section className="mt-6 border-t border-[var(--border-soft)] pt-5"><h3 className="eyebrow mb-2">Credential</h3><div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">
          {selected.candidates.map((candidate) => {
            const item = credential(candidate.credential_id)
            const chosen = candidate.credential_id === selectedCandidateId
            const content = <><Provider value={item?.provider ?? selected.resource.provider ?? "kinu"} label={false} /><span className="min-w-0 flex-1"><span className="text-[11px] font-semibold">{item?.display_name ?? "Credential"}</span><span className="mt-1 block text-[9px] leading-4 text-[var(--ink-muted)]">{candidate.reasons.join(" · ")}</span></span>{selected.candidates.length > 1 && <span className={`text-[9px] font-semibold ${chosen ? "text-[var(--ink)]" : "text-[var(--ink-muted)]"}`}>{chosen ? "Selected" : "Select"}</span>}</>
            return selected.candidates.length === 1
              ? <div key={candidate.credential_id} className="flex items-start gap-3 py-4">{content}</div>
              : <button key={candidate.credential_id} type="button" aria-pressed={chosen} onClick={() => setSelectedCandidateId(candidate.credential_id)} className="focus-ring flex w-full items-start gap-3 rounded-lg py-4 text-left">{content}</button>
          })}
          {selected.candidates.length === 0 && <div className="py-5 text-[10px] text-[var(--ink-muted)]">No managed credential matches this incident.</div>}
        </div></section>}
      </DetailCard>
      <Modal isOpen={dismissing} onClose={() => setDismissing(false)} title="Dismiss incident?" actions={<Button variant="danger" disabled={dismiss.isPending} onClick={() => dismiss.mutate()}>{dismiss.isPending ? "Dismissing…" : "Dismiss incident"}</Button>}>
        <Field label="Reason"><SelectControl value={dismissalReason} onChange={(event) => setDismissalReason(event.target.value)}>{dismissalReasons.map((reason) => <option key={reason}>{reason}</option>)}</SelectControl></Field>
      </Modal>
    </DetailPage>
  }

  return <div className="page">
    <PageHeader eyebrow="Operations" title="Incidents" />
    <DemoAlert />
    <Toolbar value={search} onChange={setSearch} placeholder="Search incidents, repositories, or services" onClear={() => { setSearch(""); setStatus("all") }} filters={[{ label: "Status", value: status, defaultValue: "all", onChange: (event) => setStatus(event.target.value), children: <><option value="all">All incidents</option><option value="open">Open incidents</option><option value="action-required">Action required</option><option value="rotation-started">Rotation started</option><option value="resolved">Resolved</option><option value="dismissed">Dismissed</option></> }]} />
    <Table responsive>
      <TableHeader><TableRow><TableHead>Source</TableHead><TableHead>Affected resource</TableHead><TableHead>Credential</TableHead><TableHead>Severity</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody columns={6} emptyMessage={search.trim() ? "No matching incidents." : "No incidents in this view."}>{rows.map((incident) => {
        const resource = affectedResource(incident, graph.data)
        return <TableRow key={incident.id}>
          <TableCell primary><div className="font-medium">{titleCase(incident.source)}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{incidentReason(incident.kind)}</div></TableCell>
          <TableCell label="Affected resource" wide><div>{resource.name}</div>{resource.detail && <div className="mt-1 max-w-[340px] truncate text-[9px] text-[var(--ink-muted)]" title={resource.detail}>{resource.detail}</div>}</TableCell>
          <TableCell label="Credential" wide>{credential(incident.credential_id)?.display_name ?? "Unconfirmed"}</TableCell>
          <TableCell label="Severity"><Badge variant={severityVariant(incident.severity)}>{incident.severity}</Badge></TableCell>
          <TableCell label="Status"><Badge variant={statusVariant(incident.status)}>{titleCase(incident.status)}</Badge></TableCell>
          <TableCell action className="pr-0"><Button variant="ghost" size="sm" className="pr-1" onClick={() => openIncident(incident)}>View details <ChevronRight className="size-3.5" /></Button></TableCell>
        </TableRow>
      })}</TableBody>
    </Table>
  </div>
}
