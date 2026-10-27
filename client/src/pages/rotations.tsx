import { Sparkles } from "lucide-react"
import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Check, ChevronDown, ChevronRight, Clock3, LoaderCircle, Paperclip, Pause, Plus, Reply, X } from "lucide-react"
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
import type { ComputerUseActivity, RotationHistory, RotationRun, RunStageActivity, StageName } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

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

function latestActivity(history: RotationHistory | undefined, stage: StageName): RunStageActivity | undefined {
  return history?.stages.filter((item) => item.stage === stage).at(-1)
}

function triggerName(source: string) {
  if (["schedule", "scheduler"].includes(source)) return "Scheduled rotation"
  if (["manual", "console", "dashboard"].includes(source)) return "Manual rotation"
  return titleCase(source)
}

function activeStageSummary(stage: StageName, status: RotationRun["status"]) {
  if (status === "paused") return stage === "approval" ? "Waiting for revocation approval" : "Action required"
  if (["failed", "cleanup-required"].includes(status)) return "Action required"
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
    planner: "Rotation Planning and Recovery Agent",
    playbook: "Playbook Builder Agent",
    operator: "Console Operator Agent",
  }[agent]
}

function currentStageExecutor(run: RotationRun): { label: "Agent" | "Executor"; value: string } {
  if (run.status === "recovering") return { label: "Agent", value: "Rotation Planning and Recovery Agent" }
  if (run.stage === "preflight" && run.browser_playbook_version) return { label: "Agent", value: "Inventory and Exposure Agent" }
  if (run.stage === "plan" && run.browser_playbook_version) return { label: "Agent", value: "Rotation Planning and Recovery Agent" }
  if (["create", "revoke"].includes(run.stage) && run.browser_playbook_version) return { label: "Agent", value: "Console Operator Agent" }
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

function ComputerUseInputImage({ runId, activity }: { runId: string; activity: ComputerUseActivity }) {
  const frame = useQuery({
    queryKey: ["rotations", runId, "computer-use", activity.id, "image"],
    queryFn: () => api.getComputerUseInputImage(runId, activity.id),
    staleTime: Infinity,
  })
  const [imageUrl, setImageUrl] = useState("")

  useEffect(() => {
    if (!frame.data) {
      setImageUrl("")
      return
    }
    const value = URL.createObjectURL(frame.data)
    setImageUrl(value)
    return () => URL.revokeObjectURL(value)
  }, [frame.data])

  return <div className="grid aspect-video place-items-center overflow-hidden rounded-xl bg-[var(--surface-soft)]">
      {frame.isLoading && <LoaderCircle className="size-5 animate-spin text-[var(--ink-muted)]" />}
      {frame.error && <div className="px-6 text-center text-[11px] text-[var(--red)]">{frame.error.message}</div>}
      {imageUrl && <img src={imageUrl} alt="Sanitised browser image sent to the agent" className="size-full object-contain" />}
    </div>
}

function ChatMessage({ sender, outgoing = false, dark = false, children }: { sender: ReactNode; outgoing?: boolean; dark?: boolean; children: ReactNode }) {
  return <div className={`flex ${outgoing ? "justify-end" : "justify-start"}`}>
    <div className="w-fit max-w-[min(100%,720px)]">
      <div className="mb-1.5 text-left text-[9px] font-semibold text-[var(--ink-muted)]">{sender}</div>
      <div className={`rounded-2xl border px-4 py-3 text-[10px] leading-5 ${outgoing || dark ? "border-[var(--ink)] bg-[var(--ink)] text-white" : "border-[var(--border)] bg-white text-[var(--ink)]"}`}>
        {children}
      </div>
    </div>
  </div>
}

function ComputerUseStream({ runId, activities, secretStoreName }: { runId: string; activities: ComputerUseActivity[]; secretStoreName: string }) {
  const turns = [...new Set(activities.map((activity) => activity.turn))]
  const latest = activities.at(-1)
  const running = Boolean(latest && !["execution"].includes(latest.phase) && latest.status !== "completed" && latest.status !== "failed")
  return <div className="mt-5">
    <div className="flex items-center gap-2 text-[9px] font-semibold text-[var(--ink-muted)]">Computer Use{running && <LoaderCircle className="size-3 animate-spin" />}</div>
    <div className="mt-4 space-y-8">
      {turns.map((turn) => {
        const events = activities.filter((activity) => activity.turn === turn)
        const input = events.find((activity) => activity.phase === "input")
        const thought = events.filter((activity) => activity.phase === "thought").map((activity) => activity.content ?? "").join("")
        const response = events.filter((activity) => activity.phase === "response" && activity.content).map((activity) => activity.content).join("")
        const proposal = events.find((activity) => activity.phase === "proposal")
        const validation = events.find((activity) => activity.phase === "validation")
        const execution = events.find((activity) => activity.phase === "execution")
        const receipt = execution ?? validation
        const receiptLabel = execution
          ? execution.status === "succeeded" ? "completed" : execution.status === "paused" ? "paused" : "failed"
          : validation?.status === "validated" ? "validated" : "rejected"
        return <div key={turn} className="space-y-4">
          {input && <>
            <ChatMessage sender="Kinu" dark>
              <span>{input.prompt}</span>
            </ChatMessage>
            <div className="max-w-[720px]">
              <div className="mb-1.5 flex items-center gap-1.5 text-[9px] font-semibold text-[var(--ink-muted)]">
                <Paperclip className="size-3" aria-hidden="true" />
                From browser
              </div>
              <div className="rounded-2xl border border-[var(--border)] bg-white p-2">
                <ComputerUseInputImage runId={runId} activity={input} />
              </div>
            </div>
          </>}
          {(thought || response || proposal) && <ChatMessage sender={<span className="inline-flex items-center gap-1.5"><Reply className="size-3" aria-hidden="true" /><AgentMark />Agent</span>}>
            {thought && <div className="mb-3"><div className="text-[9px] font-semibold text-[var(--ink-muted)]">Thought summary</div><div className="mt-1">{thought}</div></div>}
            {response && <div>{response}</div>}
            {proposal && <div className={`${thought || response ? "mt-3" : ""} space-y-2`}>
              {proposal.intent && <div>{proposal.intent}</div>}
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="font-semibold">Function call</span>
                <span>{titleCase(proposal.action ?? "action")}</span>
                {proposal.safety_decision && <span className="text-[var(--ink-muted)]">· {titleCase(proposal.safety_decision)}</span>}
              </div>
              {proposal.arguments && <pre className="overflow-x-auto whitespace-pre-wrap rounded-xl bg-[var(--surface-soft)] px-3 py-2 font-mono text-[9px] leading-5">{JSON.stringify(proposal.arguments, null, 2)}</pre>}
            </div>}
          </ChatMessage>}
          {receipt && <div className="flex items-center gap-2 text-[10px] text-[var(--ink-soft)]">
            {receiptLabel === "completed" ? <span aria-hidden="true">✅</span> : receiptLabel === "validated" ? <Check className="size-3.5 text-[var(--green)]" aria-hidden="true" /> : receiptLabel === "paused" ? <Pause className="size-3.5" aria-hidden="true" /> : <X className="size-3.5 text-[var(--red)]" aria-hidden="true" />}
            {receiptLabel === "completed" ? <span className="font-semibold text-[var(--ink)]">{input?.effect === "create-credential" ? `Credential added to ${secretStoreName}` : input?.effect === "revoke-credential" ? "Previous credential revoked" : "Step completed"}</span> : <span><strong className="font-semibold text-[var(--ink)]">Kinu {receiptLabel}</strong>{receipt.target ? ` · ${receipt.target}` : ""}</span>}
          </div>}
        </div>
      })}
    </div>
  </div>
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
    {titleCase(run.status)}
  </Badge>
}

export function RotationsPage({ activeRunId, onSelectRotation, onNavigateApproval, onNavigateControls, onNavigateConnection }: { activeRunId?: string; onSelectRotation: (runId: string) => void; onNavigateApproval: () => void; onNavigateControls: (credentialId: string, controlVersionId: string) => void; onNavigateConnection: (connectionId: string) => void }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState("all")
  const [selectedId, setSelectedId] = useState(activeRunId ?? "")
  const [selectedStage, setSelectedStage] = useState<StageName | null>("trigger")
  const [creating, setCreating] = useState(false)
  const [credentialId, setCredentialId] = useState("")
  const [reason, setReason] = useState("Routine credential rotation")
  const [urgency, setUrgency] = useState<"routine" | "urgent" | "emergency">("routine")
  const [runs, graph, history, connections] = useQueries({ queries: [
    { queryKey: ["rotations"], queryFn: () => api.getRotations(), refetchInterval: (): number | false => selectedId && finished(queryClient.getQueryData<RotationRun[]>(["rotations"])?.find((run) => run.id === selectedId)?.status) ? false : 5_000 },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
    { queryKey: ["rotations", selectedId, "history"], queryFn: () => api.getRotationHistory(selectedId), enabled: Boolean(selectedId), refetchInterval: (): number | false => finished(queryClient.getQueryData<RotationRun[]>(["rotations"])?.find((run) => run.id === selectedId)?.status) ? false : 5_000 },
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
  ] })
  const create = useMutation({ mutationFn: () => {
    const selectedCredential = graph.data!.credentials.find((item) => item.id === credentialId)!
    return api.startRotation({ credential_id: selectedCredential.id, control_version: selectedCredential.control_version, reason: reason.trim(), urgency })
  }, onSuccess: async (run) => {
    await queryClient.invalidateQueries({ queryKey: ["rotations"] })
    setSelectedId(run.id)
    onSelectRotation(run.id)
    setCreating(false)
  } })

  useEffect(() => { if (activeRunId) setSelectedId(activeRunId) }, [activeRunId])
  const liveSelection = runs.data?.find((run) => run.id === selectedId)
  const liveStage = liveSelection?.stage
  const liveStatus = liveSelection?.status
  useEffect(() => {
    if (liveStage) setSelectedStage(liveStage)
    if (finished(liveStatus)) {
      void queryClient.invalidateQueries({ queryKey: ["rotations", selectedId, "history"] })
      void queryClient.invalidateQueries({ queryKey: ["graph"] })
    }
  }, [liveStage, liveStatus, selectedId, queryClient])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (runs.data ?? []).filter((run) => {
      const credential = graph.data?.credentials.find((item) => item.id === run.credential_id)
      const matchesSearch = !term || `${credential?.display_name ?? ""} ${run.stage} ${run.status} ${run.trigger.source}`.toLowerCase().includes(term)
      if (!matchesSearch) return false
      if (filter === "active") return ["pending", "running", "paused", "recovering"].includes(run.status)
      if (filter === "scheduled") return run.trigger.source === "schedule" && run.status === "pending"
      if (filter === "completed") return ["cancelled", "completed", "compensated"].includes(run.status)
      if (filter === "failed") return ["failed", "cleanup-required"].includes(run.status)
      return true
    })
  }, [filter, graph.data?.credentials, runs.data, search])

  if ([runs, graph, connections].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [runs, graph, connections].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selected = runs.data!.find((run) => run.id === selectedId)
  const availableCredentials = graph.data!.credentials.filter((item) => !runs.data!.some((run) => run.credential_id === item.id && !["cancelled", "completed", "compensated", "failed", "cleanup-required"].includes(run.status)))

  function openCreate() {
    setCredentialId(availableCredentials[0]?.id ?? "")
    setReason("Routine credential rotation")
    setUrgency("routine")
    create.reset()
    setCreating(true)
  }

  if (selected) {
    const credential = graph.data!.credentials.find((item) => item.id === selected.credential_id)
    const secretStoreName = (connections.data!.find((item) => item.id === credential?.secret_store_connection_id)?.display_name ?? "configured secret store").split("·")[0].trim()
    const stageIndex = stages.findIndex((stage) => stage.id === selected.stage)
    return <div className="page">
      <PageHeader eyebrow="Operations / Rotations" title={credential?.display_name ?? "Rotation"} titlePrefix={<Provider value={credential?.provider ?? "kinu"} label={false} />} onBack={() => { setSelectedId(""); onSelectRotation("") }} actions={selected.status === "paused" && selected.stage === "approval" ? <Button onClick={onNavigateApproval}>Review approval <Clock3 className="size-3.5" /></Button> : undefined} />
      <DetailCard>
        <div>
          <div className="space-y-1" aria-label={`Rotation is at ${titleCase(selected.stage)}`}>
          {stages.map((stage, index) => {
            const activity = latestActivity(history.data, stage.id)
            const computerUse = history.data?.computer_use.filter((item) => item.stage === stage.id) ?? []
            const attempts = history.data?.stages.filter((item) => item.stage === stage.id) ?? []
            const completed = Boolean(activity && ["succeeded", "recovered"].includes(activity.status))
            const current = index === stageIndex && selected.status !== "completed"
            const failed = activity?.status === "failed" || (current && ["failed", "cleanup-required"].includes(selected.status))
            const paused = activity?.status === "paused" || (current && selected.status === "paused")
            const hasActivityDetails = Boolean((activity && (activity.details.length || activity.agent_decisions.length || activity.browser_actions.length || activity.reason)) || computerUse.length)
            const liveExecutor = current && !activity ? currentStageExecutor(selected) : null
            const expandable = hasActivityDetails || Boolean(liveExecutor)
            const expanded = expandable && selectedStage === stage.id
            const summary = current ? activeStageSummary(stage.id, selected.status) : activity?.summary
            const needsConnectionRecovery = current && selected.failure?.code === "provider-authentication-expired" && credential?.connection_id
            return <section key={stage.id} className="relative">
              {index < stages.length - 1 && <span aria-hidden="true" className={`pointer-events-none absolute bottom-[-14px] left-[17.5px] top-[30px] w-px ${completed ? "bg-[var(--ink)]" : "bg-[var(--border)]"}`} />}
              <button type="button" onClick={() => expandable && setSelectedStage(expanded ? null : stage.id)} aria-expanded={expanded} className={`focus-ring relative grid w-full grid-cols-[20px_minmax(0,1fr)_20px] items-start gap-3 rounded-lg px-2 py-2.5 text-left focus-visible:outline-[var(--border)] focus-visible:outline-offset-0 ${expandable ? "cursor-pointer" : "cursor-default"}`}>
                <StageMarker completed={completed} current={current} failed={failed} paused={paused} />
                <span className="min-w-0">
                  <span className={`block text-[11px] font-semibold ${current || completed ? "text-[var(--ink)]" : "text-[var(--ink-muted)]"}`}>{stage.label}</span>
                  {summary && <span className={`mt-0.5 block text-[9px] ${failed ? "text-[var(--red)]" : current ? "text-[var(--ink-soft)]" : "text-[var(--ink-muted)]"}`}>{summary}</span>}
                </span>
                {expandable && <ChevronDown className={`mt-0.5 size-4 shrink-0 text-[var(--ink-muted)] transition-transform ${expanded ? "rotate-180" : ""}`} />}
              </button>
              {expanded && <div className="ml-10 px-2 pb-5 pt-1">
                {liveExecutor && <div><div className="text-[9px] font-semibold text-[var(--ink-muted)]">{liveExecutor.label}</div><div className="mt-1 text-[10px] font-semibold text-[var(--ink)]">{liveExecutor.value}</div></div>}
                {activity && <>
                {activity.reason && <p className="mb-4 text-[11px] leading-5 text-[var(--red)]">{activity.reason}</p>}
                {needsConnectionRecovery && <Button size="sm" variant="secondary" className="mb-5" onClick={() => onNavigateConnection(needsConnectionRecovery)}>Open connection <ArrowUpRight className="size-3.5" /></Button>}
                {activity.details.filter((detail) => !(computerUse.length && detail.label === "Method" && detail.value === "Computer Use")).length > 0 && <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">{activity.details.filter((detail) => !(computerUse.length && detail.label === "Method" && detail.value === "Computer Use")).map((detail) => {
                  const controlsAction = stage.id === "trigger" && detail.label === "Configured trigger" && !["manual", "console", "dashboard"].includes(selected.trigger.source)
                  return <div key={`${detail.label}-${detail.value}`}><div className="text-[9px] font-semibold text-[var(--ink-muted)]">{detail.label}</div><div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] leading-5 text-[var(--ink)]"><span>{detail.value}</span>{controlsAction && <button type="button" className="focus-ring inline-flex items-center gap-1 rounded-md font-semibold text-[var(--ink-soft)] hover:text-[var(--ink)]" onClick={() => onNavigateControls(selected.credential_id, selected.control_version)}>View controls <ArrowUpRight className="size-3" /></button>}</div></div>
                })}</div>}
                {activity.agent_decisions.filter((decision) => !(computerUse.length && decision.agent === "operator")).length > 0 && <div className={`${activity.details.length > 0 ? "mt-5" : ""} space-y-4`}>{activity.agent_decisions.filter((decision) => !(computerUse.length && decision.agent === "operator")).map((decision, decisionIndex) => <div key={`${decision.agent}-${decisionIndex}`}><div className="text-[9px] font-semibold text-[var(--ink-muted)]">{agentName(decision.agent)}</div><div className="mt-1 text-[10px] font-semibold text-[var(--ink)]">{decision.decision}</div><div className="mt-1 text-[10px] leading-5 text-[var(--ink-soft)]">{decision.explanation}</div></div>)}</div>}
                {computerUse.length > 0 && <ComputerUseStream runId={selected.id} activities={computerUse} secretStoreName={secretStoreName} />}
                {!computerUse.length && activity.browser_actions.length > 0 && <div className="mt-5"><div className="text-[9px] font-semibold text-[var(--ink-muted)]">Console Operator Agent</div><div className="mt-3 space-y-3">{activity.browser_actions.map((action) => <div key={action.step_id}><div className="text-[10px] font-semibold">{titleCase(action.operation)}</div><div className="mt-1 text-[10px] leading-5 text-[var(--ink-soft)]">{action.objective}</div><div className="mt-0.5 text-[9px] text-[var(--ink-muted)]">{action.outcome}</div></div>)}</div></div>}
                {attempts.length > 1 && <div className="mt-5 text-[9px] text-[var(--ink-muted)]">Attempt {attempts.length}</div>}
                </>}
              </div>}
            </section>
          })}
          {history.isLoading && <div className="grid min-h-24 place-items-center"><LoaderCircle className="size-4 animate-spin text-[var(--ink-muted)]" /></div>}
          {history.error && <div className="py-4 text-[11px] text-[var(--red)]">{history.error.message}</div>}
          </div>
        </div>
      </DetailCard>
    </div>
  }

  return <div className="page">
    <PageHeader eyebrow="Operations" title="Rotations" actions={<Button onClick={openCreate} disabled={!availableCredentials.length}><Plus className="size-3.5" /> Start rotation</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search rotations" onClear={() => { setSearch(""); setFilter("all") }} filters={[{ label: "Status", value: filter, defaultValue: "all", onChange: (event) => setFilter(event.target.value), children: <><option value="all">All statuses</option><option value="active">Active</option><option value="scheduled">Scheduled</option><option value="completed">Completed</option><option value="failed">Failed</option></> }]} />
    <Table>
      <TableHeader><TableRow><TableHead>Credential</TableHead><TableHead>Stage</TableHead><TableHead>Status</TableHead><TableHead>Trigger</TableHead><TableHead>Updated</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody>{filtered.map((run) => {
        const credential = graph.data!.credentials.find((item) => item.id === run.credential_id)
        const needsApproval = run.status === "paused" && run.stage === "approval"
        return <TableRow key={run.id}>
          <TableCell><button className="focus-ring flex items-center gap-3 rounded-lg text-left font-medium hover:underline" onClick={() => { setSelectedId(run.id); onSelectRotation(run.id) }}><Provider value={credential?.provider ?? "kinu"} label={false} />{credential?.display_name ?? "Credential"}</button></TableCell>
          <TableCell>{titleCase(run.stage)}</TableCell>
          <TableCell><RunStatus run={run} /></TableCell>
          <TableCell className="text-[var(--ink-soft)]">{triggerName(run.trigger.source)}</TableCell>
          <TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(run.updated_at)}</TableCell>
          <TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={() => { if (needsApproval) onNavigateApproval(); else { setSelectedId(run.id); onSelectRotation(run.id) } }}>{needsApproval ? "Review approval" : "View details"} <ChevronRight className="size-3.5" /></Button></div></TableCell>
        </TableRow>
      })}</TableBody>
    </Table>
    <Modal isOpen={creating} onClose={() => setCreating(false)} title="Start rotation" actions={<Button onClick={() => create.mutate()} disabled={!credentialId || !reason.trim() || create.isPending}>{create.isPending ? "Starting…" : "Start rotation"}</Button>}>
      <div className="space-y-4"><Field label="Credential"><SelectControl value={credentialId} onChange={(event) => setCredentialId(event.target.value)}>{availableCredentials.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</SelectControl></Field><Field label="Urgency"><SelectControl value={urgency} onChange={(event) => setUrgency(event.target.value as typeof urgency)}><option value="routine">Routine</option><option value="urgent">Urgent</option><option value="emergency">Emergency</option></SelectControl></Field><Field label="Reason"><textarea className={`${formControl} h-24 py-3`} value={reason} onChange={(event) => setReason(event.target.value)} /></Field>{create.error && <div role="alert" className="rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{create.error.message}</div>}</div>
    </Modal>
  </div>
}

function AgentMark() { return <Sparkles className="size-3.5" aria-hidden="true" /> }
