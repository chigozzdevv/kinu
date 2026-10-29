import { useEffect, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { Icon } from "@iconify/react"
import builderIcon from "@iconify-icons/logos/builder-io-icon.js"
import { Check, LoaderCircle, MonitorCog, Pause, X } from "lucide-react"
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

function ComputerUseImage({ runId, activity }: { runId: string; activity: ComputerUseActivity }) {
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

  return <div className="grid aspect-video place-items-center overflow-hidden rounded-xl bg-[var(--surface-soft)]">
    {frame.isLoading && <LoaderCircle className="size-5 animate-spin text-[var(--ink-muted)]" />}
    {frame.error && <div className="px-6 text-center text-[11px] text-[var(--red)]">{frame.error.message}</div>}
    {imageUrl && <img src={imageUrl} alt="Redacted browser screenshot" className="size-full object-contain" />}
  </div>
}

function receiptText(receipt: ComputerUseActivity, secretStoreName: string): string {
  if (receipt.status === "validated") return "Action approved"
  if (receipt.status === "paused") return "Action paused"
  if (receipt.status !== "succeeded") return "Action failed"
  if (receipt.stage === "create") return `Credential saved to ${secretStoreName}`
  if (receipt.stage === "revoke") return "Previous credential revoked"
  return "Step completed"
}

function ExecutionResponse({ receipt }: { receipt: ComputerUseActivity }) {
  const response = {
    status: receipt.status,
    ...(receipt.result ? { provider_id: receipt.result.provider_id } : {}),
    ...(receipt.result?.secret_reference ? { stored: true } : {}),
  }
  return <div className="mt-3 border-t border-white/15 pt-3">
    <div className="mb-1 text-[9px] font-semibold text-white/60">Response</div>
    <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-5">{JSON.stringify(response, null, 2)}</pre>
  </div>
}

export function RunActivity({ runId, activities, secretStoreName }: { runId: string; activities: ComputerUseActivity[]; secretStoreName: string }) {
  const turns = [...new Set(activities.map((activity) => activity.turn))]
  return <div className="mt-5 max-w-[720px] space-y-8">
    {turns.map((turn) => {
      const events = activities.filter((activity) => activity.turn === turn)
      const proposal = events.find((activity) => activity.phase === "proposal")
      const execution = events.find((activity) => activity.phase === "execution")
      const validation = events.find((activity) => activity.phase === "validation")
      const receipt = execution ?? validation
      const completed = execution?.status === "succeeded"
      return <div key={turn} className="space-y-4">
        {proposal?.image_reference && <Message actor="browser" attachment><ComputerUseImage runId={runId} activity={proposal} /></Message>}
        {proposal && <Message actor="builder">
          {proposal.content && <p>{proposal.content}</p>}
          <div className={`${proposal.content ? "mt-3" : ""} flex flex-wrap items-baseline gap-x-2 gap-y-1`}><strong className="font-semibold">{titleCase(proposal.action ?? "action")}</strong><span>{proposal.intent}</span></div>
          {Object.keys(proposal.arguments).length > 0 && <details className="mt-3">
            <summary className="cursor-pointer text-[9px] font-semibold text-[var(--ink-muted)]">Details</summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-xl bg-[var(--surface-soft)] px-3 py-2 font-mono text-[9px] leading-5">{JSON.stringify(proposal.arguments, null, 2)}</pre>
          </details>}
        </Message>}
        {receipt && <Message actor="kinu">
          <div className="flex items-center gap-2">
            {completed || receipt.status === "validated" ? <Check className="size-3.5 shrink-0" aria-hidden="true" /> : receipt.status === "paused" ? <Pause className="size-3.5 shrink-0" aria-hidden="true" /> : <X className="size-3.5 shrink-0" aria-hidden="true" />}
            <span>{receiptText(receipt, secretStoreName)}</span>
          </div>
          {execution && <ExecutionResponse receipt={execution} />}
        </Message>}
        {proposal && !receipt && <Message actor="kinu"><span className="inline-flex items-center gap-2"><LoaderCircle className="size-3 animate-spin" aria-hidden="true" />Working…</span></Message>}
      </div>
    })}
  </div>
}
