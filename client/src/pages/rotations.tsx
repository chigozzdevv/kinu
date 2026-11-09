import { useEffect, useMemo, useState } from "react"
import { isBlockingRotation, planStepState } from "../lib/rotation-state"
import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Check, ChevronDown, ChevronRight, Clock3, LoaderCircle, Pause, Plus, X } from "lucide-react"
import { RunActivity } from "../components/activity"
import { DetailCard } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, SelectControl, formControl } from "../components/workspace"
import type {
  RotationHistory,
  RotationRun,
  RunStageActivity,
  RuntimeDeploymentProgress,
  StageName,
} from "../types"
import { api } from "../lib/api"
import { formatDate, rotationTrigger, titleCase } from "../lib/format"
import { useOrganisation } from "../lib/useorganisation"

const stages: Array<{ id: StageName; label: string }> = [
  { id: "trigger", label: "Trigger" },
  { id: "preflight", label: "Preflight" },
  { id: "plan", label: "Plan" },
  { id: "create", label: "Create" },
  { id: "store", label: "Store" },
  { id: "deploy", label: "Deploy" },
  { id: "verify", label: "Verify" },
  { id: "rollout", label: "Rollout" },
  { id: "observe", label: "Observe" },
  { id: "approval", label: "Approval" },
  { id: "revoke", label: "Revoke" },
  { id: "complete", label: "Complete" },
]

const planSteps: Array<{ id: StageName; label: string }> = [
  { id: "create", label: "Create a replacement key" },
  { id: "store", label: "Store the replacement securely" },
  { id: "deploy", label: "Deploy to connected apps" },
  { id: "verify", label: "Verify the replacement and deployment" },
  { id: "rollout", label: "Roll out the replacement" },
  { id: "observe", label: "Observe the replacement" },
  { id: "approval", label: "Check revocation approval" },
  { id: "revoke", label: "Revoke and verify the previous key" },
  { id: "complete", label: "Complete the rotation" },
]

const preflightLabels: Record<string, string> = {
  "controls-checked": "Key controls checked",
  "approver-checked": "Approver available",
  "provider-checked": "Provider connection checked",
  "credential-checked": "Current key checked",
  "apps-checked": "Connected apps and verification checked",
  "provider-ready": "Provider connection ready",
  "credential-known": "Current credential identified",
  "scopes-known": "Credential permissions identified",
  "playbook-eligible": "Provider procedure ready",
  "management-authenticated": "Provider access authenticated",
  "store-ready": "Secret storage ready",
  "consumers-known": "App mappings available",
  "runtime-ready": "Connected apps ready",
  "verifier-ready": "Verification ready",
  "overlap-supported": "Key overlap supported",
  "mutation-declared": "Provider operations available",
  "no-conflict": "No conflicting rotation",
  "approvers-known": "Approver available",
}

function PlanProgress({ history, run, actual }: { history: RotationHistory | undefined; run: RotationRun; actual?: StageName[] }) {
  const steps = actual?.length ? actual.flatMap((id) => planSteps.filter((step) => step.id === id)) : planSteps
  return <ol aria-label="Rotation plan" className="mt-5 space-y-3">
    {steps.map((step) => {
      const state = planStepState(history, run, step.id)
      const done = state === "done"
      const current = state === "current"
      return <li key={step.id} className="flex items-start gap-3 text-[12px] leading-5">
        {done ? <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-[#198754] text-white" aria-label="Completed"><Check className="size-3" strokeWidth={3} /></span>
          : state === "stopped" ? <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-[var(--red)] text-white" aria-label="Stopped"><X className="size-3" strokeWidth={3} /></span>
          : current ? <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border border-[var(--ink-soft)]" aria-label="Current step"><span className="size-1.5 rounded-full bg-[var(--ink-soft)]" /></span>
            : <span className="mt-0.5 size-4 shrink-0 rounded-full border border-[var(--border)]" aria-label="Not completed" />}
        <span className={done ? "text-[var(--ink-soft)] line-through decoration-[var(--ink-muted)]" : "text-[var(--ink)]"}>{step.label}</span>
      </li>
    })}
  </ol>
}

function deploymentTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value))
}

function secureDeploymentUrl(value: string | null) {
  if (!value) return null
  try {
    const candidate = /^[a-z][a-z\d+.-]*:\/\//i.test(value) ? value : `https://${value}`
    const url = new URL(candidate)
    return url.protocol === "https:" ? url.href : null
  } catch {
    return null
  }
}

function DeploymentProgress({ deployments, active }: {
  deployments: RuntimeDeploymentProgress[]
  active: boolean
}) {
  if (!deployments.length) return null
  return <div className="mt-5 space-y-5" aria-label="Deployment status">
    {deployments.map((deployment) => {
      const events = deployment.events.length > 0
        ? deployment.events
        : deployment.status
          ? [{ status: deployment.status, label: deployment.status, observed_at: null }]
          : []
      const lastIndex = events.length - 1
      const last = events.at(-1)
      const url = last?.status === "ready" ? secureDeploymentUrl(deployment.url) : null
      return <div key={deployment.binding_id}>
        <div className="text-[10px] font-semibold text-[var(--ink-muted)]">
          {titleCase(deployment.provider)} · {deployment.service}
          {deployment.environment ? ` · ${titleCase(deployment.environment)}` : ""}
        </div>
        {events.length > 0 ? <ol className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-2">
          {events.map((event, index) => {
            const current = active && index === lastIndex && ["queued", "building"].includes(event.status)
            const failed = event.status === "failed"
            const completed = !current && !failed && (event.status === "ready" || index < lastIndex)
            return <li key={`${event.status}-${event.observed_at ?? "current"}`} className="flex items-center gap-1.5">
              {index > 0 && <span aria-hidden="true" className="mr-0.5 text-[11px] text-[var(--ink-muted)]">→</span>}
              <span className={`grid size-4 shrink-0 place-items-center rounded-full ${failed ? "bg-[var(--red)] text-white" : completed ? "bg-[#198754] text-white" : "border border-[#198754] text-[#198754]"}`} aria-label={failed ? "Deployment failed" : completed ? "Status reached" : "Current deployment status"}>
                {failed ? <X className="size-2.5" strokeWidth={3} /> : completed ? <Check className="size-2.5" strokeWidth={3} /> : <span className={`size-1.5 rounded-full bg-[#198754] ${current ? "motion-safe:animate-pulse" : ""}`} />}
              </span>
              <span className={`text-[11px] ${failed ? "text-[var(--red)]" : "text-[var(--ink)]"}`}>{titleCase(event.label.toLowerCase())}</span>
              {event.observed_at && <time dateTime={event.observed_at} title="Time Kinu observed this status" className="whitespace-nowrap text-[10px] tabular-nums text-[var(--ink-muted)]">{deploymentTime(event.observed_at)}</time>}
            </li>
          })}
        </ol> : <p className="mt-3 text-[11px] text-[var(--ink-muted)]">Waiting for deployment status…</p>}
        {url && <a href={url} target="_blank" rel="noreferrer" className="focus-ring mt-2 inline-flex max-w-full items-center gap-1.5 break-all rounded text-[11px] text-[var(--ink-soft)] underline decoration-[var(--border)] underline-offset-4 hover:text-[var(--ink)]">
          {url}<ArrowUpRight className="size-3 shrink-0" aria-hidden="true" />
        </a>}
      </div>
    })}
  </div>
}

function latestActivity(history: RotationHistory | undefined, stage: StageName): RunStageActivity | undefined {
  return history?.stages.filter((item) => item.stage === stage).at(-1)
}

function activeStageSummary(stage: StageName, status: RotationRun["status"]) {
  if (status === "paused") return stage === "approval" ? "Waiting for revocation approval" : "Action required"
  if (status === "cleanup-required") return "Recovery required"
  if (status === "failed") return "Stopped"
  if (status === "recovering") return "Recovering safely"
  const summaries: Record<StageName, string> = {
    trigger: "Starting rotation",
    preflight: "Checking access and dependencies",
    plan: "Building the rotation plan",
    create: "Creating the replacement credential",
    store: "Storing the replacement securely",
    deploy: "Deploying the candidate revision",
    verify: "Verifying the replacement",
    rollout: "Moving traffic to the replacement",
    observe: "Observing the replacement",
    approval: "Evaluating revocation controls",
    revoke: "Revoking the previous credential",
    complete: "Completing the rotation",
  }
  return summaries[stage]
}

function agentName(agent: "inventory" | "planner" | "playbook" | "operator") {
  return {
    inventory: "Inventory and Exposure Agent",
    planner: "Builder AI",
    playbook: "Playbook Builder Agent",
    operator: "Builder AI",
  }[agent]
}

function currentStageExecutor(run: RotationRun): { label: "Agent" | "Executor"; value: string } {
  if (run.status === "recovering") return { label: "Agent", value: "Rotation Planning and Recovery Agent" }
  if (run.stage === "plan") return { label: "Agent", value: "Builder AI" }
  if (["create", "revoke"].includes(run.stage) && run.browser_playbook_version) return { label: "Agent", value: "Builder AI" }
  const executors: Record<StageName, string> = {
    trigger: "Workflow",
    preflight: "Workflow",
    plan: "Workflow",
    create: "Provider connection",
    store: "Secret-store connection",
    deploy: "Runtime connection",
    verify: "Verification Service",
    rollout: "Runtime connection",
    observe: "Verification Service",
    approval: "Control gate",
    revoke: "Provider connection",
    complete: "Workflow",
  }
  return { label: "Executor", value: executors[run.stage] }
}

function StageMarker({ completed, current, failed, paused }: { completed: boolean; current: boolean; failed: boolean; paused: boolean }) {
  if (failed) return <span className="grid size-5 shrink-0 place-items-center rounded-full bg-[var(--red)] text-white" aria-label="Failed"><X className="size-3" strokeWidth={2.5} /></span>
  if (paused) return <span className="grid size-5 shrink-0 place-items-center rounded-full border border-[var(--ink)] text-[var(--ink)]" aria-label="Paused"><Pause className="size-2.5" fill="currentColor" /></span>
  if (completed) return <span className="grid size-5 shrink-0 place-items-center rounded-full bg-[var(--ink)] text-white" aria-label="Completed"><Check className="size-3" strokeWidth={2.75} /></span>
  if (current) return <span className="grid size-5 shrink-0 place-items-center rounded-full border border-[var(--ink)] text-[var(--ink)]" aria-label="In progress"><LoaderCircle className="size-3 animate-spin" /></span>
  return <span className="size-5 shrink-0 rounded-full border border-[var(--border)]" aria-label="Not started" />
}


function variant(run: RotationRun) {
  if (["failed", "cleanup-required"].includes(run.status)) return "danger" as const
  if (run.status === "paused") return "warning" as const
  if (["cancelled", "completed", "compensated"].includes(run.status)) return "healthy" as const
  if (run.status === "pending") return "neutral" as const
  return "active" as const
}

function finished(status?: RotationRun["status"]) {
  return Boolean(status && ["completed", "failed", "cancelled", "compensated", "cleanup-required"].includes(status))
}

function RunStatus({ run }: { run: RotationRun }) {
  const moving = run.status === "running" || run.status === "recovering"
  return <Badge variant={variant(run)} className="gap-1.5">
    {moving && <LoaderCircle className="size-3 animate-spin" aria-hidden="true" />}
    {run.status === "compensated" ? run.recovery_mode === "rollback" ? "Rolled back" : "Recovered" : titleCase(run.status)}
  </Badge>
}

export function RotationsPage({ activeRunId, onSelectRotation, onNavigateApproval, onNavigateControls, onNavigateConnection, onNavigateIncident, onNavigateAi }: { activeRunId?: string; onSelectRotation: (runId: string) => void; onNavigateApproval: () => void; onNavigateControls: (credentialId: string, controlVersionId: string) => void; onNavigateConnection: (connectionId: string) => void; onNavigateIncident: (incidentId: string) => void; onNavigateAi: () => void }) {
  const queryClient = useQueryClient()
  const { active } = useOrganisation()
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState("all")
  const [selectedId, setSelectedId] = useState(activeRunId ?? "")
  const [selectedStage, setSelectedStage] = useState<StageName | null>("trigger")
  const [creating, setCreating] = useState(false)
  const [confirmingRecovery, setConfirmingRecovery] = useState(false)
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [credentialId, setCredentialId] = useState("")
  const [reason, setReason] = useState("Routine credential rotation")
  const [urgency, setUrgency] = useState<"routine" | "urgent" | "emergency">("routine")
  const runs = useInfiniteQuery({
    queryKey: ["rotations", "pages"],
    initialPageParam: "",
    queryFn: ({ pageParam }) => api.getRotations(pageParam || undefined),
    getNextPageParam: (page) => page.length === 100 ? page.at(-1)?.id : undefined,
    refetchInterval: 5_000,
  })
  const records = useMemo(() => runs.data?.pages.flat() ?? [], [runs.data])
  const selectedRun = useQuery({
    queryKey: ["rotation", selectedId],
    queryFn: () => api.getRotation(selectedId),
    enabled: Boolean(selectedId),
    refetchInterval: (query): number | false => query.state.data?.status === "cleanup-required" || !finished(query.state.data?.status) ? query.state.data?.stage === "preflight" ? 1_000 : 5_000 : false,
  })
  const linkedIncident = useQuery({ queryKey: ["rotations", selectedId, "incident", selectedRun.data?.trigger.event_id],
    queryFn: () => api.getRotationIncident(selectedId, selectedRun.data!.trigger.event_id), enabled: Boolean(selectedId && selectedRun.data) })
  const [graph, history, connections, currentRuns] = useQueries({ queries: [
    { queryKey: ["graph"], queryFn: () => api.getGraph(), refetchInterval: 10_000 },
    { queryKey: ["rotations", selectedId, "history"], queryFn: () => api.getRotationHistory(selectedId), enabled: Boolean(selectedId), refetchInterval: (): number | false => {
      const run = queryClient.getQueryData<RotationRun>(["rotation", selectedId])
      return finished(run?.status) ? false : run?.stage === "deploy" || run?.stage === "preflight" || run?.browser_playbook_version && ["create", "revoke"].includes(run.stage) ? 1_000 : 5_000
    } },
    { queryKey: ["connections"], queryFn: () => api.getConnections(), refetchInterval: 10_000 },
    { queryKey: ["rotations", "current"], queryFn: () => api.getCurrentRotations(), refetchInterval: 5_000 },
  ] })
  const create = useMutation({ mutationFn: () => {
    const selectedCredential = availableCredentials.find((item) => item.id === credentialId)
    if (!selectedCredential) throw new Error("Select a credential to rotate")
    return api.startRotation({ credential_id: selectedCredential.id, control_version: selectedCredential.control_version, reason: reason.trim(), urgency })
  }, onSuccess: async (run) => {
    queryClient.setQueryData(["rotation", run.id], run)
    await queryClient.invalidateQueries({ queryKey: ["rotations"] })
    setSelectedId(run.id)
    onSelectRotation(run.id)
    setCreating(false)
  } })
  const retry = useMutation({
    mutationFn: () => {
      const run = selectedRun.data
      if (!run) throw new Error("Rotation is unavailable")
      return api.startRotation({
        credential_id: run.credential_id,
        control_version: run.control_version,
        reason: "Retry after failed rotation",
        urgency: run.trigger?.urgency ?? "urgent",
      })
    },
    onSuccess: async (run) => {
      queryClient.setQueryData(["rotation", run.id], run)
      await queryClient.invalidateQueries({ queryKey: ["rotations"] })
      setSelectedId(run.id)
      onSelectRotation(run.id)
    },
  })
  const recover = useMutation({
    mutationFn: () => api.recoverRotation(selectedId, selectedRun.data!.revision),
    onSuccess: async (run) => {
      queryClient.setQueryData(["rotation", run.id], run)
      setConfirmingRecovery(false)
      await queryClient.invalidateQueries({ queryKey: ["rotations"] })
      await queryClient.invalidateQueries({ queryKey: ["rotations", run.id, "history"] })
    },
  })
  const cancel = useMutation({
    mutationFn: () => api.cancelRotation(selectedId, selectedRun.data!.revision),
    onSuccess: async (run) => {
      queryClient.setQueryData(["rotation", run.id], run)
      setConfirmingCancel(false)
      await queryClient.invalidateQueries({ queryKey: ["rotations"] })
      await queryClient.invalidateQueries({ queryKey: ["rotations", run.id, "history"] })
    },
  })

  useEffect(() => { if (activeRunId) setSelectedId(activeRunId) }, [activeRunId])
  const liveSelection = selectedRun.data
  const liveStage = liveSelection?.stage
  const liveStatus = liveSelection?.status
  useEffect(() => {
    if (liveStage) setSelectedStage(liveStage)
    if (finished(liveStatus)) {
      void queryClient.invalidateQueries({ queryKey: ["rotations", selectedId, "history"] })
      void queryClient.invalidateQueries({ queryKey: ["graph"] })
      void queryClient.invalidateQueries({ queryKey: ["incidents"] })
      void queryClient.invalidateQueries({ queryKey: ["incident"] })
      void queryClient.invalidateQueries({ queryKey: ["approvals"] })
      void queryClient.invalidateQueries({ queryKey: ["overview"] })
    }
  }, [liveStage, liveStatus, selectedId, queryClient])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return records.filter((run) => {
      const credential = graph.data?.credentials.find((item) => item.id === run.credential_id)
      const matchesSearch = !term || `${credential?.display_name ?? ""} ${run.stage} ${run.status} ${run.trigger.source}`.toLowerCase().includes(term)
      if (!matchesSearch) return false
      if (filter === "active") return isBlockingRotation(run)
      if (filter === "scheduled") return run.trigger.source === "schedule" && run.status === "pending"
      if (filter === "completed") return ["cancelled", "completed", "compensated"].includes(run.status)
      if (filter === "failed") return ["failed", "cleanup-required"].includes(run.status)
      return true
    })
  }, [filter, graph.data?.credentials, records, search])

  if ([runs, graph, connections, currentRuns].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [runs, graph, connections, currentRuns].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  if (selectedId && selectedRun.isLoading) return <div className="page"><Loading /></div>
  if (selectedId && selectedRun.error) return <div className="page"><PageHeader eyebrow="Operations / Rotations" title="Rotation" onBack={() => { setSelectedId(""); onSelectRotation("") }} /><Failure error={selectedRun.error} /></div>
  const selected = selectedRun.data
  const availableCredentials = graph.data!.credentials.filter((item) => !currentRuns.data!.some((run) => run.credential_id === item.id && run.organisation_id === item.organisation_id && isBlockingRotation(run)))

  function openCreate() {
    setCredentialId(availableCredentials[0]?.id ?? "")
    setReason("Routine credential rotation")
    setUrgency("routine")
    create.reset()
    setCreating(true)
  }

  function retryRotation(credentialId: string) {
    setCredentialId(availableCredentials.some((item) => item.id === credentialId) ? credentialId : availableCredentials[0]?.id ?? "")
    setReason("Retry after failed rotation")
    setUrgency("urgent")
    create.reset()
    setCreating(true)
  }

  if (selected) {
    const credential = graph.data!.credentials.find((item) => item.id === selected.credential_id)
    const stageIndex = stages.findIndex((stage) => stage.id === selected.stage)
    return <div className="page rotation-run-page">
      <PageHeader titleInBreadcrumb eyebrow="Operations / Rotations" title={credential?.display_name ?? "Rotation"} titlePrefix={<Provider value={credential?.provider ?? "kinu"} label={false} />} onBack={() => { setSelectedId(""); onSelectRotation("") }} actions={<>
        {selected.status === "paused" && selected.stage === "approval" && <Button onClick={onNavigateApproval}>Review approval <Clock3 className="size-3.5" /></Button>}
        {selected.status === "cleanup-required" && active.role === "administrator" && <Button onClick={() => { recover.reset(); setConfirmingRecovery(true) }}>Recover rotation</Button>}
        {selected.status === "failed" && ["trigger", "preflight", "plan"].includes(selected.stage) && <Button onClick={() => retry.mutate()} disabled={retry.isPending}>{retry.isPending ? "Retrying…" : "Retry run"}</Button>}
      </>} />
      <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-[11px] text-[var(--ink-soft)]">
        <span className="font-semibold text-[var(--ink)]">{selected.status === "compensated" ? selected.recovery_mode === "rollback" ? "Rolled back" : "Recovered" : titleCase(selected.status)}</span>
        <span aria-hidden="true">·</span><span>Started {formatDate(selected.created_at, true)}</span>
      </div>
      <DetailCard>
        <div>
          {selected.failure && <p role="status" className="mb-5 text-[11px] leading-5 text-[var(--red)]">{selected.failure.message}</p>}
          {selected.failure?.code === "builder-setup-required" && <Button size="sm" variant="secondary" className="mb-5" onClick={onNavigateAi}>Open AI settings <ArrowUpRight className="size-3.5" /></Button>}
          {selected.status === "compensated" && <p className="mb-5 text-[11px] text-[var(--ink-soft)]">{selected.recovery_mode === "rollback" ? "Recovery completed · previous key restored" : "Recovery completed · replacement key active"}</p>}
          <div className="space-y-1" aria-label={`Rotation is at ${titleCase(selected.stage)}`}>
          {stages.map((stage, index) => {
            const activity = latestActivity(history.data, stage.id)
            const computerUse = history.data?.computer_use.filter((item) => item.stage === stage.id) ?? []
            const hasFrame = computerUse.some((item) => Boolean(item.image_reference))
            const details = activity?.details.filter((detail) =>
              !(hasFrame && detail.label === "Method" && detail.value === "Browser")
              && !(stage.id === "trigger" && detail.label === "Source" && selected.trigger.source === "agent-native")
            ) ?? []
            const attempts = history.data?.stages.filter((item) => item.stage === stage.id) ?? []
            const liveChecks = stage.id === "preflight" ? history.data?.preflight_checks ?? [] : []
            const checks = liveChecks.length ? liveChecks : activity?.status === "succeeded" && stage.id === "preflight" ? activity.checks : []
            const recovered = selected.status === "compensated" && selected.recovery_stage === stage.id && selected.recovery_evidence_ids.length > 0
            const completed = recovered || Boolean(activity && ["succeeded", "recovered"].includes(activity.status))
            const current = index === stageIndex && !["completed", "cancelled", "compensated"].includes(selected.status)
            const failed = !recovered && (activity?.status === "failed" || (current && ["failed", "cleanup-required"].includes(selected.status)))
            const paused = activity?.status === "paused" || (current && selected.status === "paused")
            const deploymentProgress = stage.id === "deploy" ? history.data?.deployment_progress ?? [] : []
            const hasActivityDetails = Boolean((activity && (details.length || activity.agent_decisions.length || activity.browser_actions.length || activity.reason || (stage.id === "preflight" && activity.checks.length) || stage.id === "plan")) || computerUse.length || checks.length || deploymentProgress.length)
            const liveExecutor = current && !activity && !computerUse.length ? currentStageExecutor(selected) : null
            const expandable = hasActivityDetails || Boolean(liveExecutor)
            const expanded = expandable && selectedStage === stage.id
            const summary = recovered ? "Recovered" : current ? activeStageSummary(stage.id, selected.status) : activity?.summary
            const needsConnectionRecovery = current && selected.failure?.code === "provider-authentication-expired" && credential?.connection_id
            return <section key={stage.id} className="relative">
              {index < stages.length - 1 && <span aria-hidden="true" className="pointer-events-none absolute bottom-[-14px] left-[17.5px] top-[30px] w-px bg-[var(--border)]" />}
              <button type="button" onClick={() => expandable && setSelectedStage(expanded ? null : stage.id)} aria-expanded={expanded} className={`focus-ring relative grid w-full grid-cols-[20px_minmax(0,1fr)_20px] items-start gap-3 rounded-lg px-2 py-2.5 text-left focus-visible:outline-[var(--border)] focus-visible:outline-offset-0 ${expandable ? "content-hover cursor-pointer" : "cursor-default"}`}>
                <StageMarker completed={completed} current={current} failed={failed} paused={paused} />
                <span className="min-w-0">
                  <span className={`block text-[12px] font-semibold ${current || completed ? "text-[var(--ink)]" : "text-[var(--ink-muted)]"}`}>{stage.label}</span>
                  {summary && <span className={`mt-0.5 block text-[10px] ${failed ? "text-[var(--red)]" : current ? "text-[var(--ink-soft)]" : "text-[var(--ink-muted)]"}`}>{summary}</span>}
                </span>
                {expandable && <ChevronDown className={`mt-0.5 size-4 shrink-0 text-[var(--ink-muted)] transition-transform ${expanded ? "rotate-180" : ""}`} />}
              </button>
              {expanded && <div className="ml-10 px-2 pb-5 pt-1">
                {liveExecutor && <div><div className="text-[10px] font-semibold text-[var(--ink-muted)]">{liveExecutor.label}</div><div className="mt-1 text-[12px] font-semibold text-[var(--ink)]">{liveExecutor.value}</div></div>}
                {checks.some((check) => preflightLabels[check]) && <ul aria-label="Preflight checks" className="mb-5 mt-2 space-y-2.5">{checks.filter((check) => preflightLabels[check]).map((check) => <li key={check} className="flex items-center gap-2.5 text-[11px] text-[var(--ink-soft)]"><span className="grid size-4 shrink-0 place-items-center rounded-full bg-[#198754] text-white"><Check className="size-3" strokeWidth={3} /></span><span className="line-through decoration-[var(--ink-muted)]">{preflightLabels[check]}</span></li>)}</ul>}
                {activity && <>
                {activity.reason && <p className={`mb-4 text-[11px] leading-5 ${recovered ? "text-[var(--ink-soft)]" : "text-[var(--red)]"}`}>{recovered ? "This step stopped earlier; recovery is complete." : activity.reason}</p>}
                {needsConnectionRecovery && <Button size="sm" variant="secondary" className="mb-5" onClick={() => onNavigateConnection(needsConnectionRecovery)}>Open connection <ArrowUpRight className="size-3.5" /></Button>}
                {stage.id === "trigger" && linkedIncident.data && <div className="mb-4 text-[11px] leading-5"><div className="text-[10px] font-semibold text-[var(--ink-muted)]">Incident</div>{linkedIncident.data.description && <p className="mt-1 whitespace-pre-line text-[var(--ink-soft)]">{linkedIncident.data.description}</p>}<button type="button" className="focus-ring mt-1 inline-flex items-center gap-1 rounded font-semibold text-[var(--ink)]" onClick={() => onNavigateIncident(linkedIncident.data!.id)}>View incident <ArrowUpRight className="size-3" /></button></div>}
                {details.length > 0 && <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">{details.map((detail) => {
                  const controlsAction = stage.id === "trigger" && detail.label === "Configured trigger" && rotationTrigger(selected.trigger) !== "Manual rotation"
                  return <div key={`${detail.label}-${detail.value}`}><div className="text-[10px] font-semibold text-[var(--ink-muted)]">{detail.label}</div><div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] leading-5 text-[var(--ink)]"><span>{detail.value}</span>{controlsAction && <button type="button" className="focus-ring inline-flex items-center gap-1 rounded-md font-semibold text-[var(--ink-soft)] hover:text-[var(--ink)]" onClick={() => onNavigateControls(selected.credential_id, selected.control_version)}>View controls <ArrowUpRight className="size-3" /></button>}</div></div>
                })}</div>}
                {stage.id === "deploy" && <DeploymentProgress deployments={deploymentProgress} active={current && ["running", "recovering"].includes(selected.status)} />}
                {activity.agent_decisions.filter((decision) => !(computerUse.length && decision.agent === "operator")).length > 0 && <div className={`${details.length > 0 ? "mt-5" : ""} space-y-4`}>{activity.agent_decisions.filter((decision) => !(computerUse.length && decision.agent === "operator")).map((decision, decisionIndex) => <div key={`${decision.agent}-${decisionIndex}`}><div className="text-[10px] font-semibold text-[var(--ink-muted)]">{agentName(decision.agent)}</div>{decision.agent !== "planner" && <div className="mt-1 text-[12px] font-semibold text-[var(--ink)]">{decision.decision}</div>}<div className="mt-1 whitespace-pre-line text-[12px] leading-5 text-[var(--ink-soft)]">{decision.explanation}</div></div>)}</div>}
                {stage.id === "plan" && (activity.agent_input?.length ?? 0) > 0 && <details className="mt-4 text-[11px] text-[var(--ink-soft)]"><summary className="focus-ring w-fit cursor-pointer rounded font-medium">Input to Builder AI</summary><dl className="mt-3 grid gap-x-8 gap-y-3 sm:grid-cols-2">{activity.agent_input!.map((item) => <div key={item.label}><dt className="text-[9px] text-[var(--ink-muted)]">{item.label}</dt><dd className="mt-1 text-[10px] text-[var(--ink)]">{item.value}</dd></div>)}</dl></details>}
                {stage.id === "plan" && (activity.agent_input?.length ?? 0) > 0 && activity.status === "succeeded" && <p className="mt-4 text-[11px] font-medium text-[var(--ink-soft)]">Plan validated against this key’s controls.</p>}
                {stage.id === "plan" && ["succeeded", "recovered"].includes(activity.status) && <PlanProgress history={history.data} run={selected} actual={activity.plan_steps} />}
                {!computerUse.length && activity.browser_actions.length > 0 && <div className="mt-5"><div className="text-[10px] font-semibold text-[var(--ink-muted)]">Builder AI</div><div className="mt-3 space-y-3">{activity.browser_actions.map((action) => <div key={action.step_id}><div className="text-[10px] font-semibold">{titleCase(action.operation)}</div><div className="mt-1 text-[12px] leading-5 text-[var(--ink-soft)]">{action.objective}</div><div className="mt-0.5 text-[9px] text-[var(--ink-muted)]">{action.outcome}</div></div>)}</div></div>}
                {attempts.length > 1 && <div className="mt-5 text-[9px] text-[var(--ink-muted)]">Attempt {attempts.length}</div>}
                {!computerUse.length && <p className="mt-4 text-[10px] text-[var(--ink-muted)]">{formatDate(activity.completed_at, true)}</p>}
                </>}
                {computerUse.length > 0 && <RunActivity runId={selected.id} activities={computerUse} />}
              </div>}
            </section>
          })}
          {history.isLoading && <div className="grid min-h-24 place-items-center"><LoaderCircle className="size-4 animate-spin text-[var(--ink-muted)]" /></div>}
          {history.error && <div className="py-4 text-[11px] text-[var(--red)]">{history.error.message}</div>}
          </div>
        </div>
      </DetailCard>
      <Modal isOpen={confirmingRecovery} onClose={() => setConfirmingRecovery(false)} title="Recover rotation" actions={<Button disabled={recover.isPending} onClick={() => recover.mutate()}>{recover.isPending ? "Starting…" : "Check and recover"}</Button>}>
        <p className="text-[12px] leading-6 text-[var(--ink-soft)]">Kinu will check the recorded provider and deployment state, then restore the previous key or finish a verified rotation. Unresolved actions stay held for review.</p>
        {recover.error && <p role="alert" className="mt-4 text-[11px] text-[var(--red)]">{recover.error.message}</p>}
      </Modal>
      <Modal isOpen={confirmingCancel} onClose={() => setConfirmingCancel(false)} title="Cancel rotation" actions={<Button disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? "Cancelling…" : "Cancel run"}</Button>}>
        <p className="text-[12px] leading-6 text-[var(--ink-soft)]">Cancelling ends this run and releases the credential for a new rotation. Provider state may be unresolved — inspect the saved evidence before rotating again.</p>
        {cancel.error && <p role="alert" className="mt-4 text-[11px] text-[var(--red)]">{cancel.error.message}</p>}
      </Modal>
    </div>
  }

  return <div className="page">
    <PageHeader eyebrow="Operations" title="Rotations" actions={<Button onClick={openCreate} disabled={!availableCredentials.length}><Plus className="size-3.5" /> Start rotation</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search rotations" onClear={() => { setSearch(""); setFilter("all") }} filters={[{ label: "Status", value: filter, defaultValue: "all", onChange: (event) => setFilter(event.target.value), children: <><option value="all">All statuses</option><option value="active">Active</option><option value="scheduled">Scheduled</option><option value="completed">Completed</option><option value="failed">Failed</option></> }]} />
    <Table responsive>
      <TableHeader><TableRow><TableHead>Credential</TableHead><TableHead>Stage</TableHead><TableHead>Status</TableHead><TableHead>Trigger</TableHead><TableHead>Updated</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody columns={6} emptyMessage={search.trim() || filter !== "all" ? "No matching rotations." : "No rotations yet."}>{filtered.map((run) => {
        const credential = graph.data!.credentials.find((item) => item.id === run.credential_id)
        const needsApproval = run.status === "paused" && run.stage === "approval"
        return <TableRow key={run.id}>
          <TableCell primary><button className="focus-ring flex items-center gap-3 rounded-lg text-left font-medium hover:underline" onClick={() => { setSelectedId(run.id); onSelectRotation(run.id) }}><Provider value={credential?.provider ?? "kinu"} label={false} />{credential?.display_name ?? "Credential"}</button></TableCell>
          <TableCell label="Stage">{titleCase(run.stage)}</TableCell>
          <TableCell label="Status"><RunStatus run={run} /></TableCell>
          <TableCell label="Trigger" className="text-[var(--ink-soft)]">{rotationTrigger(run.trigger)}</TableCell>
          <TableCell label="Updated" className="text-[10px] text-[var(--ink-soft)]">{formatDate(run.updated_at)}</TableCell>
          <TableCell action className="pr-0"><Button className="pr-1" variant="ghost" size="sm" onClick={() => { if (needsApproval) onNavigateApproval(); else { setSelectedId(run.id); onSelectRotation(run.id) } }}>{needsApproval ? "Review approval" : "View details"} <ChevronRight className="content-chevron size-3.5" /></Button></TableCell>
        </TableRow>
      })}</TableBody>
    </Table>
    {runs.hasNextPage && <div className="mt-5 flex justify-center"><Button variant="secondary" disabled={runs.isFetchingNextPage} onClick={() => { void runs.fetchNextPage() }}>{runs.isFetchingNextPage ? "Loading…" : "Load more"}</Button></div>}
    <Modal isOpen={creating} onClose={() => setCreating(false)} title="Start rotation" actions={<Button onClick={() => create.mutate()} disabled={!credentialId || !reason.trim() || create.isPending}>{create.isPending ? "Starting…" : "Start rotation"}</Button>}>
      <div className="space-y-4"><Field label="Credential"><SelectControl value={credentialId} onChange={(event) => setCredentialId(event.target.value)}>{availableCredentials.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</SelectControl></Field><Field label="Urgency"><SelectControl value={urgency} onChange={(event) => setUrgency(event.target.value as typeof urgency)}><option value="routine">Routine</option><option value="urgent">Urgent</option><option value="emergency">Emergency</option></SelectControl></Field><Field label="Reason"><textarea className={`${formControl} h-24 py-3`} value={reason} onChange={(event) => setReason(event.target.value)} /></Field>{create.error && <div role="alert" className="rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{create.error.message}</div>}</div>
    </Modal>
  </div>
}
