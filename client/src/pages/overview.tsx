import { useQueries } from "@tanstack/react-query"
import { ArrowUpRight, Check, ChevronRight, ChevronUp } from "lucide-react"
import { useState } from "react"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading, Notice } from "../components/state"
import { Button } from "../components/ui/button"
import { api } from "../lib/api"
import { titleCase } from "../lib/format"
import { incidentReason } from "../lib/incident"
import type { NavItem } from "../lib/navigation"
import type { RotationRun } from "../types"

function failureTask(run: RotationRun) {
  if (run.failure?.code === "provider-authentication-expired") return "Reconnect provider"
  if (run.status === "cleanup-required") return "Complete recovery"
  return "Resolve failed rotation"
}

function activityLabel(run: RotationRun) {
  if (run.status === "completed") return "Rotation completed"
  if (run.status === "compensated") return run.recovery_mode === "rollback" ? "Rotation rolled back" : "Rotation recovered"
  if (run.status === "cancelled") return "Rotation cancelled"
  if (run.status === "recovering") return "Recovery started"
  if (run.status === "pending") return "Rotation queued"
  if (run.stage === "trigger") return "Rotation started"
  return `${titleCase(run.stage)} started`
}

type OverviewProps = {
  onNavigate: (nav: NavItem) => void
  onNavigateRotation: (runId: string) => void
  onNavigateIncident: (incidentId: string) => void
  onNavigateApproval: (approvalId: string) => void
}

type QuickStartStep = {
  label: string
  done: boolean
  target: NavItem
}

function QuickStart({ steps, onNavigate }: { steps: QuickStartStep[]; onNavigate: (nav: NavItem) => void }) {
  return <div className="flex flex-col items-center">
    <h2 className="text-[15px] font-semibold tracking-[-0.025em] text-[var(--ink)]">Quick start</h2>
    <ol className="mt-6 flex w-max max-w-full flex-col items-start gap-4">
      {steps.map((step, index) => <li key={step.label}>
        {step.done
          ? <div className="inline-grid grid-cols-[20px_auto_14px] items-center gap-x-3 text-[11px] text-[var(--ink-muted)]">
            <span className="text-center font-semibold tabular-nums">{index + 1}</span>
            <span className="line-through">{step.label}</span>
            <Check className="size-3.5" aria-hidden="true" />
          </div>
          : <button
            className="focus-ring content-hover inline-grid grid-cols-[20px_auto_14px] items-center gap-x-3 rounded-lg text-left text-[11px] font-medium text-[var(--ink)]"
            onClick={() => onNavigate(step.target)}
          >
            <span className="text-center font-semibold tabular-nums text-[var(--ink-muted)]">{index + 1}</span>
            <span>{step.label}</span>
            <ChevronRight className="content-chevron size-3.5 text-[var(--ink-muted)]" aria-hidden="true" />
          </button>}
      </li>)}
    </ol>
  </div>
}

export function OverviewPage({ onNavigate, onNavigateRotation, onNavigateIncident, onNavigateApproval }: OverviewProps) {
  const [showAllAttention, setShowAllAttention] = useState(false)
  const [showAllActivities, setShowAllActivities] = useState(false)
  const [runs, incidents, approvals, graph, connections, playbooks] = useQueries({
    queries: [
      { queryKey: ["rotations"], queryFn: () => api.getRotations(), refetchInterval: 5_000 },
      { queryKey: ["incidents"], queryFn: () => api.getIncidents(), refetchInterval: 10_000 },
      { queryKey: ["approvals"], queryFn: () => api.getApprovals(), refetchInterval: 10_000 },
      { queryKey: ["graph"], queryFn: () => api.getGraph() },
      { queryKey: ["connections"], queryFn: () => api.getConnections() },
      { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
    ],
  })

  const queries = [runs, incidents, graph, connections]
  if (queries.some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = queries.find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const credentials = graph.data!.credentials.filter((item) => !item.archived_at)
  const activeConnections = connections.data!.filter((item) => !item.archived_at)
  const activePlaybooks = playbooks.data?.filter((item) => !item.archived_at) ?? []
  const credential = (id: string | null | undefined) => credentials.find((item) => item.id === id)
  const pendingApprovals = approvals.data?.filter((approval) => approval.decision === "pending" && Date.parse(approval.expires_at) > Date.now()) ?? []
  const approvalRunIds = new Set(pendingApprovals.map((approval) => approval.run_id))
  const actionableIncidents = incidents.data!.filter((incident) => incident.status === "action-required")
  const interruptedRuns = runs.data!.filter((run) => ["failed", "cleanup-required"].includes(run.status) || (run.status === "paused" && !approvalRunIds.has(run.id)))
  const recentRuns = [...runs.data!]
    .filter((run) => !["failed", "cleanup-required", "paused"].includes(run.status))
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
    .slice(0, 5)

  const metrics: Array<{ label: string; value: number | null; target: NavItem }> = [
    { label: "Credentials", value: credentials.length, target: "credentials" },
    { label: "Connections", value: activeConnections.length, target: "connections" },
    { label: "Playbooks", value: playbooks.data ? activePlaybooks.length : null, target: "playbooks" },
    { label: "Rotations", value: runs.data!.length, target: "rotations" },
  ]

  const actionRows = [
    ...pendingApprovals.map((approval) => {
      const run = runs.data!.find((item) => item.id === approval.run_id)
      const item = credential(run?.credential_id)
      return {
        id: `approval:${approval.id}`,
        task: "Review revocation",
        resource: item?.display_name ?? "Credential",
        provider: item?.provider ?? "kinu",
        reason: "Approval is required before revocation.",
        action: "Review",
        open: () => onNavigateApproval(approval.id),
      }
    }),
    ...actionableIncidents.map((incident) => {
      const candidateId = incident.credential_id ?? (incident.candidates.length === 1 ? incident.candidates[0].credential_id : null)
      const item = credential(candidateId)
      return {
        id: `incident:${incident.id}`,
        task: incident.credential_id ? "Review incident" : "Confirm affected credential",
        resource: item?.display_name ?? incident.resource.service ?? incident.resource.repository ?? "Affected resource",
        provider: item?.provider ?? incident.resource.provider ?? "kinu",
        reason: incidentReason(incident.kind),
        action: "Review",
        open: () => onNavigateIncident(incident.id),
      }
    }),
    ...interruptedRuns.map((run) => {
      const item = credential(run.credential_id)
      return {
        id: `rotation:${run.id}`,
        task: run.status === "paused" ? "Resume rotation" : failureTask(run),
        resource: item?.display_name ?? "Credential",
        provider: item?.provider ?? "kinu",
        reason: run.failure?.message ?? `Rotation stopped during ${titleCase(run.stage)}.`,
        action: "Open",
        open: () => onNavigateRotation(run.id),
      }
    }),
  ]

  const browserConnectionExists = activeConnections.some((connection) => connection.interface === "browser")
  const quickStart = [
    { label: "Add a connection", done: activeConnections.length > 0, target: "connections" as NavItem },
    { label: "Add a credential", done: credentials.length > 0, target: "credentials" as NavItem },
    ...(browserConnectionExists ? [{ label: "Add a Computer Use playbook", done: activePlaybooks.length > 0, target: "playbooks" as NavItem }] : []),
    { label: "Start a rotation", done: runs.data!.length > 0, target: "credentials" as NavItem },
  ]
  const visibleActionRows = showAllAttention ? actionRows : actionRows.slice(0, 2)
  const visibleRecentRuns = showAllActivities ? recentRuns : recentRuns.slice(0, 3)
  const emptyOverview = credentials.length === 0 && activeConnections.length === 0 && runs.data!.length === 0 && actionRows.length === 0

  if (emptyOverview) return <div className="page flex min-h-[calc(100vh-56px)] items-center justify-center py-12 lg:min-h-[calc(100vh-48px)]">
    <QuickStart steps={quickStart} onNavigate={onNavigate} />
  </div>

  return <div className="page">
    <PageHeader title="Overview" />
    {approvals.error && <Notice>Approvals are currently unavailable.</Notice>}
    {playbooks.error && <Notice>Computer Use playbooks are currently unavailable.</Notice>}

    <section aria-label="Inventory" className="mb-10 grid grid-cols-2 gap-3 xl:grid-cols-4">
      {metrics.map((metric) => <button
        key={metric.label}
        className="focus-ring content-hover flex min-h-24 items-center justify-between rounded-2xl border border-[var(--border)] bg-white px-5 text-left"
        onClick={() => onNavigate(metric.target)}
      >
        <span>
          <span className="block text-[11px] font-medium text-[var(--ink-soft)]">{metric.label}</span>
          <span className="mt-2 block text-[28px] font-semibold leading-none tracking-[-0.05em] text-[var(--ink)]">{metric.value ?? "—"}</span>
        </span>
        <ChevronRight className="content-chevron size-4 text-[var(--ink-muted)]" aria-hidden="true" />
      </button>)}
    </section>

    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.8fr)]">
      <section>
        <h2 className="mb-4 text-[15px] font-semibold tracking-[-0.025em] text-[var(--ink)]">Needs attention</h2>
        <div className="attention-panel overflow-hidden rounded-2xl border border-[var(--border)] bg-white">
          <ul className="divide-y divide-[var(--border-soft)]">
            {visibleActionRows.map((item) => <li key={item.id} className="attention-row content-hover">
              <div className="attention-task min-w-0"><div className="text-[12px] font-medium">{item.task}</div><div className="mt-1 text-[10px] leading-4 text-[var(--ink-muted)]">{item.reason}</div></div>
              <div className="attention-resource flex min-w-0 items-center gap-3"><Provider value={item.provider} label={false} /><span className="min-w-0 break-words text-[11px] font-medium">{item.resource}</span></div>
              <Button variant="ghost" size="sm" className="attention-action" onClick={item.open}>{item.action} <ChevronRight className="content-chevron size-3.5" /></Button>
            </li>)}
            {!actionRows.length && <li className="py-12 text-center text-[11px] text-[var(--ink-muted)]">Nothing needs attention.</li>}
          </ul>
          {actionRows.length > 2 && <div className="border-t border-[var(--border-soft)]">
            <button className="focus-ring content-hover flex w-full items-center justify-center gap-2 px-5 py-4 text-[11px] font-semibold text-[var(--ink-soft)] hover:text-[var(--ink)]" onClick={() => setShowAllAttention((value) => !value)}>
              {showAllAttention ? "Show less" : "View all"}
              {showAllAttention ? <ChevronUp className="size-3.5" aria-hidden="true" /> : <ArrowUpRight className="size-3.5" aria-hidden="true" />}
            </button>
          </div>}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-[15px] font-semibold tracking-[-0.025em] text-[var(--ink)]">Recent activities</h2>
        <div className="overflow-hidden rounded-2xl border border-[var(--border)] bg-white">
          {recentRuns.length ? <div className="divide-y divide-[var(--border-soft)]">
            {visibleRecentRuns.map((run) => {
              const item = credential(run.credential_id)
              return <button key={run.id} className="focus-ring content-hover flex w-full items-center gap-3 px-5 py-[18px] text-left" onClick={() => onNavigateRotation(run.id)}>
                <Provider value={item?.provider ?? "kinu"} label={false} />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-[var(--ink)]">{activityLabel(run)}</span>
                  <span className="mt-1 block truncate text-[10px] text-[var(--ink-muted)]">{item?.display_name ?? "Credential"}</span>
                </span>
                <ChevronRight className="content-chevron size-4 shrink-0 text-[var(--ink-muted)]" aria-hidden="true" />
              </button>
            })}
            {recentRuns.length > 3 && <button className="focus-ring content-hover flex w-full items-center justify-center gap-2 px-5 py-4 text-[11px] font-semibold text-[var(--ink-soft)] hover:text-[var(--ink)]" onClick={() => setShowAllActivities((value) => !value)}>
              {showAllActivities ? "Show less" : "View all"}
              {showAllActivities ? <ChevronUp className="size-3.5" aria-hidden="true" /> : <ArrowUpRight className="size-3.5" aria-hidden="true" />}
            </button>}
          </div> : <div className="grid min-h-[318px] place-items-center px-8 py-10"><QuickStart steps={quickStart} onNavigate={onNavigate} /></div>}
        </div>
      </section>
    </div>
  </div>
}
