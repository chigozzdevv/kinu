import { useEffect, useRef, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { Icon } from "@iconify/react"
import builderIcon from "@iconify-icons/logos/builder-io-icon.js"
import { Check, Expand, EyeOff, LoaderCircle, MonitorCog, Pause, X, ZoomIn, ZoomOut } from "lucide-react"
import type { ComputerUseActivity, RunStageActivity } from "../types"
import { api } from "../lib/api"
import { titleCase } from "../lib/format"

type Actor = "browser" | "builder" | "kinu"
const names: Record<Actor, string> = { browser: "Browser", builder: "Builder AI", kinu: "Kinu" }

function Avatar({ actor }: { actor: Actor }) {
  return <span className="grid size-7 shrink-0 place-items-center overflow-hidden rounded-full border border-[var(--border)] bg-white" aria-hidden="true">
    {actor === "builder" ? <Icon icon={builderIcon} className="size-4" /> : actor === "kinu" ? <img src="/favicon.svg" alt="" className="size-full" /> : <MonitorCog className="size-4 text-[var(--ink-soft)]" strokeWidth={1.6} />}
  </span>
}

function Message({ actor, attachment = false, children }: { actor: Actor; attachment?: boolean; children: ReactNode }) {
  const outgoing = actor !== "builder"
  return <div className={`flex items-start gap-2.5 ${outgoing ? "flex-row-reverse" : ""}`}>
    <Avatar actor={actor} />
    <div className={`min-w-0 ${attachment ? "w-full max-w-[40rem]" : "w-fit max-w-[68ch] [overflow-wrap:anywhere]"}`}>
      <div className={`mb-1 flex h-7 items-center text-[12px] font-semibold text-[var(--ink-soft)] ${outgoing ? "justify-end" : ""}`}>{names[actor]}</div>
      <div className={`rounded-2xl border text-[13px] leading-5 ${attachment ? "border-[var(--border)] bg-white p-2" : outgoing ? "border-[var(--ink)] bg-[var(--ink)] px-3.5 py-2.5 text-white" : "border-[var(--border)] bg-white px-3.5 py-2.5 text-[var(--ink)]"}`}>
        {children}
      </div>
    </div>
  </div>
}

function ComputerUseImage({ runId, activity, caption, hidden = false }: { runId: string; activity: ComputerUseActivity; caption: string; hidden?: boolean }) {
  const frame = useQuery({
    queryKey: ["rotations", runId, "computer-use", activity.id, "image"],
    queryFn: () => api.getComputerUseImage(runId, activity.id),
    staleTime: Infinity,
  })
  const [imageUrl, setImageUrl] = useState("")
  const dialog = useRef<HTMLDialogElement>(null)
  const [actualSize, setActualSize] = useState(false)

  useEffect(() => {
    if (!frame.data) {
      setImageUrl("")
      return
    }
    const value = URL.createObjectURL(frame.data)
    setImageUrl(value)
    return () => URL.revokeObjectURL(value)
  }, [frame.data])

  const previewClass = "grid aspect-video w-full place-items-center overflow-hidden rounded-lg bg-[var(--surface-soft)]"
  return <figure className="relative">
    {imageUrl ? <button type="button" className={`focus-ring ${previewClass} cursor-zoom-in after:absolute after:inset-0 after:rounded-xl`} aria-label={`Expand browser capture: ${caption}`} onClick={() => dialog.current?.showModal()}>
      <img src={imageUrl} alt="Redacted browser screenshot" className="size-full object-contain" />
    </button> : <div className={previewClass}>
      {frame.isLoading && <LoaderCircle className="size-5 animate-spin text-[var(--ink-muted)]" />}
      {frame.error && <div className="px-6 text-center text-[11px] text-[var(--red)]">{frame.error.message}</div>}
    </div>}
    {imageUrl && <dialog ref={dialog} className="rotation-frame-dialog" aria-label={`Browser capture: ${caption}`} onClose={() => setActualSize(false)}>
      <header className="flex items-center gap-3 border-b border-[var(--border)] px-4 py-2">
        <span className="mr-auto text-[13px] font-semibold">Browser · {caption}</span>
        <button type="button" className="focus-ring inline-flex items-center gap-1.5 rounded-md p-2 text-xs" aria-pressed={actualSize} onClick={() => setActualSize(!actualSize)}>
          {actualSize ? <ZoomOut className="size-4" /> : <ZoomIn className="size-4" />}{actualSize ? "Fit to window" : "Actual size"}
        </button>
        <button type="button" className="focus-ring rounded-md p-2" aria-label="Close browser capture" onClick={() => dialog.current?.close()}><X className="size-4" /></button>
      </header>
      <div className="rotation-frame-viewport" data-actual-size={actualSize}>
        <img src={imageUrl} alt={`Redacted browser screenshot: ${caption}`} />
      </div>
    </dialog>}
    <figcaption className="flex items-center gap-2 px-1 pt-1.5 text-[11px] leading-4 text-[var(--ink-soft)]">
      {caption}{hidden && <span className="inline-flex" title="Key hidden" role="img" aria-label="Key hidden"><EyeOff className="size-3.5" aria-hidden="true" /></span>}
      {imageUrl && <Expand className="ml-auto size-3.5" aria-hidden="true" />}
    </figcaption>
  </figure>
}

function Explanation({ text, streaming }: { text: string; streaming: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const long = text.length > 600
  const shown = long && !expanded ? text.slice(0, 600).trimEnd() + "…" : text
  return <p className="whitespace-pre-wrap" aria-live="polite" aria-busy={streaming}>
    {shown}
    {long && <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="focus-ring ml-1 rounded font-semibold underline underline-offset-2">{expanded ? "View less" : "View more"}</button>}
    {streaming && <LoaderCircle className="ml-2 inline size-3 animate-spin text-[var(--ink-muted)]" aria-label="Receiving response" />}
  </p>
}

function ActionResponse({ receipt }: { receipt: ComputerUseActivity }) {
  const symbol = receipt.status === "succeeded" ? "✅" : receipt.status === "failed" ? "❌" : null
  const Indicator = receipt.status === "paused" ? Pause : Check
  const tone = receipt.status === "paused" ? "bg-amber-500" : "bg-white/20"
  const response = {
    status: receipt.status,
    ...(receipt.result ? { provider_id: receipt.result.provider_id } : {}),
    ...(receipt.result?.secret_reference ? { stored: true } : {}),
  }
  return <div>
    <div className="flex items-center gap-2 font-semibold">
      {symbol ? <span role="img" aria-label={receipt.status === "succeeded" ? "Succeeded" : "Failed"} className="shrink-0 text-[14px] leading-none">{symbol}</span> : <span className={`grid size-4 shrink-0 place-items-center rounded-full ${tone}`} aria-hidden="true"><Indicator className="size-2.5 text-white" strokeWidth={3} /></span>}
      <span>Response</span>
    </div>
    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[12px] leading-5">{JSON.stringify(response, null, 2)}</pre>
  </div>
}

const apiAgentNames: Record<string, string> = {
  inventory: "Inventory and Exposure Agent",
  planner: "Builder AI",
  playbook: "Playbook Builder Agent",
  operator: "Builder AI",
}

export function RunApiActivity({ activity, label }: { activity: RunStageActivity; label: string }) {
  const prompt = activity.summary || `Run ${label.toLowerCase()} for this credential.`
  const status = activity.status === "succeeded" ? "Succeeded"
    : activity.status === "failed" ? "Failed"
    : activity.status === "paused" ? "Paused"
    : activity.status === "recovered" ? "Recovered"
    : titleCase(activity.status)
  return <div className="rotation-transcript mt-5 space-y-6">
    <div className="rotation-turn space-y-3">
      <Message actor="kinu">
        <Explanation text={prompt} streaming={false} />
        {activity.details.length > 0 && <dl className="mt-3 grid gap-x-6 gap-y-1.5 text-[11px] text-white/80 sm:grid-cols-2">
          {activity.details.map((detail) => <div key={`${detail.label}-${detail.value}`}>
            <dt className="text-[9px] uppercase tracking-wide text-white/50">{detail.label}</dt>
            <dd className="mt-0.5 break-words">{detail.value}</dd>
          </div>)}
        </dl>}
      </Message>
      {activity.agent_decisions.map((decision, index) => <Message key={`${decision.agent}-${index}`} actor="builder">
        <div className="text-[10px] font-semibold text-[var(--ink-muted)]">{apiAgentNames[decision.agent] ?? titleCase(decision.agent)}</div>
        <div className="mt-1 font-semibold">{decision.decision}</div>
        <p className="mt-1 whitespace-pre-line text-[12px] leading-5 text-[var(--ink-soft)]">{decision.explanation}</p>
      </Message>)}
      <Message actor="kinu">
        <div className="flex items-center gap-2 font-semibold">
          <span className={`grid size-4 shrink-0 place-items-center rounded-full ${activity.status === "succeeded" ? "bg-[var(--ink)]" : "bg-white/20"}`} aria-hidden="true">
            <Check className="size-2.5 text-white" strokeWidth={3} />
          </span>
          <span>{status}</span>
        </div>
        {activity.reason && <p className="mt-1 text-[12px] leading-5 text-white/80">{activity.reason}</p>}
        {(activity.checks.length > 0 || activity.evidence_count > 0) && <p className="mt-1 text-[10px] text-white/60">
          {activity.checks.length > 0 ? `${activity.checks.length} checks` : ""}
          {activity.checks.length > 0 && activity.evidence_count > 0 ? " · " : ""}
          {activity.evidence_count > 0 ? `${activity.evidence_count} evidence item${activity.evidence_count === 1 ? "" : "s"}` : ""}
        </p>}
      </Message>
    </div>
  </div>
}

export function RunActivity({ runId, activities }: { runId: string; activities: ComputerUseActivity[] }) {
  const turns = [...new Set(activities.map((activity) => activity.turn))]
  return <div className="rotation-transcript mt-5 space-y-6">
    {turns.map((turn) => {
      const events = activities.filter((activity) => activity.turn === turn)
      const input = events.find((activity) => activity.phase === "input")
      const proposal = events.find((activity) => activity.phase === "proposal")
      const before = input?.image_reference ? input : proposal
      const execution = events.find((activity) => activity.phase === "execution")
      const validation = events.find((activity) => activity.phase === "validation")
      const responses = events.filter((activity) => activity.phase === "response")
      const response = responses.at(-1)
      const receipt = execution ?? validation ?? (response?.status === "failed" ? response : undefined)
      const content = proposal?.content ?? response?.content
      const streaming = !proposal && response?.status === "streaming"
      return <div key={turn} className="rotation-turn space-y-3">
        {input?.prompt && <Message actor="kinu">
          <Explanation text={input.prompt} streaming={false} />
          {input.target && <p className="mt-2 text-[11px] text-white/80" title={input.intent ?? undefined}><code className="break-all">{input.target.replace(/^version_/, "pbk_")}</code></p>}
        </Message>}
        {before?.image_reference && <Message actor="browser" attachment><ComputerUseImage runId={runId} activity={before} caption="Before action" /></Message>}
        {(proposal || content) && <Message actor="builder">
          {content && <Explanation text={content} streaming={streaming} />}
          {proposal && <div className={`${content ? "mt-3" : ""} flex flex-wrap items-baseline gap-x-2 gap-y-1`}><strong className="font-semibold">{titleCase(proposal.action ?? "action")}</strong><span>{proposal.arguments.button ? `“${proposal.arguments.button}”` : proposal.intent}</span></div>}
          {proposal && Object.keys(proposal.arguments).length > 0 && <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded-xl bg-[var(--surface-soft)] px-3 py-2 font-mono text-[12px] leading-5">{JSON.stringify(proposal.arguments, null, 2)}</pre>}
        </Message>}
        {receipt && <Message actor="kinu"><ActionResponse receipt={receipt} /></Message>}
        {execution?.image_reference && <Message actor="browser" attachment><ComputerUseImage runId={runId} activity={execution} caption="After action" hidden /></Message>}
        {proposal && !receipt && <Message actor="kinu"><span className="inline-flex items-center gap-2"><LoaderCircle className="size-3 animate-spin" aria-hidden="true" />Working…</span></Message>}
      </div>
    })}
  </div>
}
