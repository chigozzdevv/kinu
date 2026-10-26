import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, ChevronRight } from "lucide-react"
import { Detail, DetailCard, DetailList } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"
import type { Approval, ApprovalEvidenceSnapshot } from "../types"

type ApprovalDecision = "approved" | "rejected"

function approvalName(approval: Approval) {
  if (approval.action_id.includes("revoke")) return "Revoke previous credential"
  return titleCase(approval.action_id.replace(/^action_/, ""))
}

function isExpired(approval: Approval, now = Date.now()) {
  return approval.decision === "pending" && Date.parse(approval.expires_at) <= now
}

function approvalStatus(approval: Approval, now = Date.now()): { label: string; variant: "warning" | "healthy" | "danger" | "neutral" } {
  if (isExpired(approval, now)) return { label: "Expired", variant: "neutral" }
  if (approval.decision === "pending") return { label: "Action required", variant: "warning" }
  if (approval.decision === "approved") return { label: "Approved", variant: "healthy" }
  if (approval.decision === "rejected") return { label: "Rejected", variant: "danger" }
  if (approval.decision === "cancelled") return { label: "Cancelled", variant: "neutral" }
  if (approval.decision === "more-evidence") return { label: "Evidence requested", variant: "neutral" }
  return { label: "Verification extended", variant: "neutral" }
}

const evidenceLabels: Array<{ checks: string[]; label: string; value: string }> = [
  { checks: ["provider-valid", "replacement-valid", "credential-valid"], label: "Provider", value: "Replacement valid" },
  { checks: ["store-valid", "secret-version-enabled", "secret-stored"], label: "Secret store", value: "Version enabled" },
  { checks: ["deployment-valid", "runtime-valid", "candidate-running"], label: "Runtime", value: "Candidate running" },
  { checks: ["old-use-clear"], label: "Previous credential", value: "No use detected" },
]

function evidenceItems(snapshot: ApprovalEvidenceSnapshot) {
  const checks = new Set(snapshot.checks)
  const items = evidenceLabels.filter((item) => item.checks.some((check) => checks.has(check)))
  return items.length ? items : [{ checks: [], label: "Snapshot", value: titleCase(snapshot.status) }]
}

export function ApprovalsPage({ initialApprovalId = "", onSelectApproval, onNavigateRotation }: { initialApprovalId?: string; onSelectApproval: (approvalId: string) => void; onNavigateRotation: (runId: string) => void }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("pending")
  const [selectedId, setSelectedId] = useState(initialApprovalId)
  const [confirming, setConfirming] = useState<ApprovalDecision | null>(null)
  const [now, setNow] = useState(Date.now())
  const [approvals, runs, graph] = useQueries({ queries: [
    { queryKey: ["approvals"], queryFn: () => api.getApprovals() },
    { queryKey: ["rotations"], queryFn: () => api.getRotations() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })
  const evidence = useQuery({
    queryKey: ["approval-evidence", selectedId],
    queryFn: () => api.getApprovalEvidence(selectedId),
    enabled: Boolean(selectedId),
  })

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (approvals.data ?? []).filter((approval) => {
      const run = runs.data?.find((item) => item.id === approval.run_id)
      const credential = graph.data?.credentials.find((item) => item.id === run?.credential_id)
      const presentation = approvalStatus(approval, now)
      const matchesStatus = status === "all"
        || (status === "expired" ? isExpired(approval, now) : status === "pending" ? approval.decision === "pending" && !isExpired(approval, now) : approval.decision === status)
      const haystack = `${approvalName(approval)} ${credential?.display_name ?? ""} ${run?.stage ?? ""} ${presentation.label}`.toLowerCase()
      return matchesStatus && (!term || haystack.includes(term))
    })
  }, [approvals.data, graph.data, now, runs.data, search, status])

  const selected = approvals.data?.find((approval) => approval.id === selectedId)
  const selectedRun = runs.data?.find((item) => item.id === selected?.run_id)
  const selectedCredential = graph.data?.credentials.find((item) => item.id === selectedRun?.credential_id)

  const openApproval = (approvalId: string) => {
    setSelectedId(approvalId)
    onSelectApproval(approvalId)
    decide.reset()
  }

  const decide = useMutation({
    mutationFn: (decision: ApprovalDecision) => api.decideApproval(selected!.id, selected!.revision, decision),
    onSuccess: async (result) => {
      queryClient.setQueryData<Approval[]>(["approvals"], (current) => current?.map((item) => item.id === result.id ? result : item))
      setConfirming(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
        queryClient.invalidateQueries({ queryKey: ["rotations"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ])
    },
  })

  if ([approvals, runs, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [approvals, runs, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  if (selected) {
    const actionable = selected.decision === "pending" && !isExpired(selected, now)
    return <div className="page">
      <PageHeader eyebrow="Operations / Approvals" title={approvalName(selected)} titlePrefix={selectedCredential ? <Provider value={selectedCredential.provider} label={false} /> : undefined} onBack={() => { setSelectedId(""); onSelectApproval(""); setConfirming(null); decide.reset() }} actions={<>{selectedRun && <Button variant="secondary" onClick={() => onNavigateRotation(selectedRun.id)}>Open rotation <ArrowUpRight className="size-3.5" /></Button>}{actionable && <><Button variant="danger" disabled={decide.isPending} onClick={() => setConfirming("rejected")}>Reject</Button><Button disabled={decide.isPending} onClick={() => setConfirming("approved")}>Approve</Button></>}</>} />
      {decide.error && <div role="alert" className="mb-5 text-[10px] text-[var(--red)]">{decide.error.message}</div>}
      <DetailCard>
        <DetailList><Detail label="Credential">{selectedCredential?.display_name ?? "Credential"}</Detail><Detail label="Requested">{formatDate(selected.created_at, true)}</Detail><Detail label="Expires">{formatDate(selected.expires_at, true)}</Detail><Detail label="Requested by">{titleCase(selected.requested_by.replace(/^actor_/, ""))}</Detail>{selected.decided_at && <Detail label="Decided">{formatDate(selected.decided_at, true)}</Detail>}</DetailList>
        <section className="mt-7">
          {evidence.isLoading ? <Loading /> : evidence.error ? <Failure error={evidence.error} /> : evidence.data ? <>
            <h3 className="mb-4 text-[11px] font-semibold">Verification</h3>
            {evidence.data.status !== "passed" && evidence.data.status !== "ready" && <div className="mb-4"><Badge variant="warning">{titleCase(evidence.data.status)}</Badge></div>}
            <DetailList>{evidenceItems(evidence.data).map((item) => <Detail key={item.label} label={item.label}>{item.value}</Detail>)}</DetailList>
          </> : null}
        </section>
      </DetailCard>
      <Modal isOpen={Boolean(confirming)} onClose={() => setConfirming(null)} title={confirming === "approved" ? "Approve revocation?" : "Reject revocation?"} actions={<Button variant={confirming === "approved" ? "primary" : "danger"} disabled={decide.isPending} onClick={() => confirming && decide.mutate(confirming)}>{decide.isPending ? "Saving…" : confirming === "approved" ? "Approve" : "Reject"}</Button>}>
        <DetailList><Detail label="Credential">{selectedCredential?.display_name ?? "Credential"}</Detail><Detail label="Action">Revoke previous credential</Detail></DetailList>
      </Modal>
    </div>
  }

  return <div className="page">
    <PageHeader eyebrow="Operations" title="Approvals" />
    <Toolbar value={search} onChange={setSearch} placeholder="Search approvals or credentials" onClear={() => { setSearch(""); setStatus("pending") }} filters={[{ label: "Status", value: status, defaultValue: "pending", onChange: (event) => setStatus(event.target.value), children: <><option value="pending">Action required</option><option value="all">All approvals</option><option value="expired">Expired</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="more-evidence">Evidence requested</option><option value="extend-observation">Verification extended</option></> }]} />
    <Table>
      <TableHeader><TableRow><TableHead>Requested action</TableHead><TableHead>Credential</TableHead><TableHead>Stage</TableHead><TableHead>Requested</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody>{rows.map((approval) => {
        const run = runs.data!.find((item) => item.id === approval.run_id)
        const credential = graph.data!.credentials.find((item) => item.id === run?.credential_id)
        const presentation = approvalStatus(approval, now)
        return <TableRow key={approval.id}>
          <TableCell><button className="focus-ring rounded-lg text-left font-medium hover:underline" onClick={() => openApproval(approval.id)}>{approvalName(approval)}</button></TableCell>
          <TableCell>{credential?.display_name ?? "Credential"}</TableCell>
          <TableCell>{titleCase(run?.stage ?? "unknown")}</TableCell>
          <TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(approval.created_at)}</TableCell>
          <TableCell><Badge variant={presentation.variant}>{presentation.label}</Badge></TableCell>
          <TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={() => openApproval(approval.id)}>{approval.decision === "pending" && !isExpired(approval, now) ? "Review" : "View details"} <ChevronRight className="size-3.5" /></Button></div></TableCell>
        </TableRow>
      })}</TableBody>
    </Table>
  </div>
}
