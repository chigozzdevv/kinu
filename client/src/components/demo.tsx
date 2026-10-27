import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "../lib/api"
import { nativeRequest, type BuilderStatus } from "../lib/native"
import { useOrganisation } from "../lib/useorganisation"
import { Button } from "./ui/button"

export function DemoAlert() {
  const { active } = useOrganisation()
  const org = active.organisation.id
  const cache = useQueryClient()
  const demo = useQuery({ queryKey: ["demo", org], queryFn: () => api.getDemo(), retry: false })
  const builder = useQuery({ queryKey: ["builder", org], queryFn: () => nativeRequest<BuilderStatus>(org, "builder/status"), enabled: demo.data?.configured === true, retry: false })
  const send = useMutation({ mutationFn: () => nativeRequest(org, "actions/alert", { requestId: crypto.randomUUID() }),
    onSuccess: async () => { await cache.invalidateQueries({ queryKey: ["incidents"] }) },
  })
  if (!demo.data?.configured || active.role === "viewer") return null
  return <div className="mb-5 flex flex-wrap items-center gap-4 rounded-xl border border-[var(--border)] p-4">
    <div className="flex-1 text-xs"><strong>Demo email alert</strong><p className="mt-1 text-[var(--ink-muted)]">Send a sample exposure alert through the incident workflow.</p></div>
    <Button disabled={send.isPending || !builder.data?.connected} onClick={() => send.mutate()}>{send.isPending ? "Queuing…" : "Send demo alert"}</Button>
    {!builder.data?.connected && <p className="w-full text-xs text-[var(--ink-muted)]">Connect Builder.io in Settings → AI first.</p>}
    {send.isSuccess && <p role="status" className="w-full text-xs">Demo alert queued. The incident will appear here after intake.</p>}
    {send.error && <p role="alert" className="w-full text-xs text-[var(--red)]">{send.error.message}</p>}
  </div>
}
