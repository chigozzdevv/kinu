import { useEffect, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { Check, LoaderCircle, Paperclip, Pause, Sparkles, X } from "lucide-react"
import type { ComputerUseActivity } from "../types"
import { api } from "../lib/api"
import { titleCase } from "../lib/format"

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

function ChatMessage({ sender, outgoing = false, children }: { sender: ReactNode; outgoing?: boolean; children: ReactNode }) {
  return <div className={`flex ${outgoing ? "justify-end" : "justify-start"}`}>
    <div className="w-fit max-w-[min(100%,720px)]">
      <div className={`mb-1.5 ${outgoing ? "text-right" : "text-left"} text-[9px] font-semibold text-[var(--ink-muted)]`}>{sender}</div>
      <div className={`rounded-2xl border px-4 py-3 text-[10px] leading-5 ${outgoing ? "border-[var(--ink)] bg-[var(--ink)] text-white" : "border-[var(--border)] bg-white text-[var(--ink)]"}`}>
        {children}
      </div>
    </div>
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
        {proposal?.image_reference && <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[9px] font-semibold text-[var(--ink-muted)]"><Paperclip className="size-3" aria-hidden="true" />Browser</div>
          <div className="rounded-2xl border border-[var(--border)] bg-white p-2"><ComputerUseImage runId={runId} activity={proposal} /></div>
        </div>}
        {proposal && <ChatMessage sender={<span className="inline-flex items-center gap-1.5"><Sparkles className="size-3.5" aria-hidden="true" />Agent</span>}>
          {proposal.content && <p>{proposal.content}</p>}
          <div className={`${proposal.content ? "mt-3" : ""} flex flex-wrap items-baseline gap-x-2 gap-y-1`}><strong className="font-semibold">{titleCase(proposal.action ?? "action")}</strong><span>{proposal.intent}</span></div>
          {Object.keys(proposal.arguments).length > 0 && <details className="mt-3">
            <summary className="cursor-pointer text-[9px] font-semibold text-[var(--ink-muted)]">Details</summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-xl bg-[var(--surface-soft)] px-3 py-2 font-mono text-[9px] leading-5">{JSON.stringify(proposal.arguments, null, 2)}</pre>
          </details>}
        </ChatMessage>}
        {receipt && <ChatMessage sender="Kinu" outgoing><div className="flex items-center gap-2">
          {completed || receipt.status === "validated" ? <Check className="size-3.5 shrink-0" aria-hidden="true" /> : receipt.status === "paused" ? <Pause className="size-3.5 shrink-0" aria-hidden="true" /> : <X className="size-3.5 shrink-0" aria-hidden="true" />}
          <span>{receiptText(receipt, secretStoreName)}</span>
        </div></ChatMessage>}
        {proposal && !receipt && <ChatMessage sender="Kinu" outgoing><span className="inline-flex items-center gap-2"><LoaderCircle className="size-3 animate-spin" aria-hidden="true" />Working…</span></ChatMessage>}
      </div>
    })}
  </div>
}
