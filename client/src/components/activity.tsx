import { useEffect, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { Icon } from "@iconify/react"
import builderIcon from "@iconify-icons/logos/builder-io-icon.js"
import { Check, LoaderCircle, MonitorCog, Pause } from "lucide-react"
import type { ComputerUseActivity } from "../types"
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
    <div className={`min-w-0 ${attachment ? "w-full max-w-[560px]" : "w-fit max-w-[calc(100%-38px)]"}`}>
      <div className={`mb-1.5 flex h-7 items-center text-[10px] font-semibold text-[var(--ink-muted)] ${outgoing ? "justify-end" : ""}`}>{names[actor]}</div>
      <div className={`rounded-2xl border text-[11px] leading-5 ${attachment ? "border-[var(--border)] bg-white p-2" : outgoing ? "border-[var(--ink)] bg-[var(--ink)] px-4 py-3 text-white" : "border-[var(--border)] bg-white px-4 py-3 text-[var(--ink)]"}`}>
        {children}
      </div>
    </div>
  </div>
}

function ComputerUseImage({ runId, activity, caption }: { runId: string; activity: ComputerUseActivity; caption: string }) {
  const frame = useQuery({
    queryKey: ["rotations", runId, "computer-use", activity.id, "image"],
    queryFn: () => api.getComputerUseImage(runId, activity.id),
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

  return <figure><div className="grid aspect-video place-items-center overflow-hidden rounded-xl bg-[var(--surface-soft)]">
    {frame.isLoading && <LoaderCircle className="size-5 animate-spin text-[var(--ink-muted)]" />}
    {frame.error && <div className="px-6 text-center text-[11px] text-[var(--red)]">{frame.error.message}</div>}
    {imageUrl && <img src={imageUrl} alt="Redacted browser screenshot" className="size-full object-contain" />}
  </div><figcaption className="px-1 pt-1.5 text-[9px] text-[var(--ink-muted)]">{caption}</figcaption></figure>
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
    <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-5">{JSON.stringify(response, null, 2)}</pre>
  </div>
}

export function RunActivity({ runId, activities }: { runId: string; activities: ComputerUseActivity[] }) {
  const turns = [...new Set(activities.map((activity) => activity.turn))]
  return <div className="mt-5 max-w-[720px] space-y-8">
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
      return <div key={turn} className="space-y-4">
        {input?.prompt && <Message actor="kinu">
          <Explanation text={input.prompt} streaming={false} />
          {input.target && <p className="mt-2 text-[9px] text-white/70" title={input.intent ?? undefined}>Playbook <code className="break-all">{input.target}</code></p>}
        </Message>}
        {before?.image_reference && <Message actor="browser" attachment><ComputerUseImage runId={runId} activity={before} caption="Before click" /></Message>}
        {(proposal || content) && <Message actor="builder">
          {content && <Explanation text={content} streaming={streaming} />}
          {proposal && <div className={`${content ? "mt-3" : ""} flex flex-wrap items-baseline gap-x-2 gap-y-1`}><strong className="font-semibold">{titleCase(proposal.action ?? "action")}</strong><span>{proposal.arguments.button ? `“${proposal.arguments.button}”` : proposal.intent}</span></div>}
          {proposal && Object.keys(proposal.arguments).length > 0 && <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-xl bg-[var(--surface-soft)] px-3 py-2 font-mono text-[9px] leading-5">{JSON.stringify(proposal.arguments, null, 2)}</pre>}
        </Message>}
        {receipt && <Message actor="kinu"><ActionResponse receipt={receipt} /></Message>}
        {execution?.image_reference && <Message actor="browser" attachment><ComputerUseImage runId={runId} activity={execution} caption="After creation · Key hidden" /></Message>}
        {proposal && !receipt && <Message actor="kinu"><span className="inline-flex items-center gap-2"><LoaderCircle className="size-3 animate-spin" aria-hidden="true" />Working…</span></Message>}
      </div>
    })}
  </div>
}
