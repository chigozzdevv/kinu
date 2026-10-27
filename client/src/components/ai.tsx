import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Detail, DetailList } from "./detail"
import { Field, SelectControl } from "./workspace"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { Loading, Failure } from "./state"
import { nativeRequest, type BuilderStatus } from "../lib/native"
import { useOrganisation } from "../lib/useorganisation"

export function AiSettings({ administrator }: { administrator: boolean }) {
  const cache = useQueryClient()
  const org = useOrganisation().active.organisation.id
  const state = useQuery({ queryKey: ["builder", org], queryFn: () => nativeRequest<BuilderStatus>(org, "builder/status"), retry: false })
  const action = useMutation({
    mutationFn: async ({ name, model }: { name: "connect" | "disconnect" | "model", model?: string }) => {
      const result = await nativeRequest<{ authorization_url?: string }>(org, `builder/${name}`, model ? { model } : {})
      if (result.authorization_url) window.location.assign(result.authorization_url)
      await cache.invalidateQueries({ queryKey: ["builder"] })
    },
  })
  if (state.isLoading) return <Loading />
  if (state.error) return <Failure error={state.error} />
  const current = state.data!
  return <div className="max-w-[700px] space-y-6">
    <p className="text-sm text-[var(--ink-muted)]">Assess incident alerts with your Builder.io credits.</p>
    <DetailList>
      <Detail label="Connection"><Badge variant={current.connected ? "healthy" : "neutral"}>{current.connected ? "Connected" : "Not connected"}</Badge></Detail>
      <Detail label="Last verified">{current.verified_at ? new Date(current.verified_at).toLocaleString() : "Not verified yet"}</Detail>
      <Detail label="Usage limit">20 AI calls per workspace per day</Detail>
    </DetailList>
    <div className="grid gap-x-8 sm:grid-cols-2">
      <Field label="Model"><SelectControl disabled={!administrator || !current.connected || action.isPending} value={current.model} onChange={(event) => action.mutate({ name: "model", model: event.target.value })}>{current.models.map((model) => <option key={model} value={model}>{model}</option>)}</SelectControl></Field>
    </div>
    {administrator && <div className="flex gap-3">
      <Button disabled={action.isPending} onClick={() => action.mutate({ name: "connect" })}>{current.connected ? "Reconnect Builder.io" : "Connect Builder.io"}</Button>
      {current.connected && <Button variant="ghost" disabled={action.isPending} onClick={() => action.mutate({ name: "disconnect" })}>Disconnect</Button>}
    </div>}
    {!administrator && <p className="text-sm text-[var(--ink-muted)]">A workspace administrator manages this connection.</p>}
    {action.isPending && <p role="status" className="text-sm">Updating connection…</p>}
    {action.error && <p role="alert" className="text-sm text-[var(--red)]">{action.error.message}</p>}
  </div>
}
