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
  return <div className="max-w-[640px] space-y-6">
    <DetailList>
      <Detail label="Builder.io"><Badge variant={current.connected ? "healthy" : "neutral"}>{current.connected ? "Connected" : "Not connected"}</Badge></Detail>
      <Detail label="Last verified">{current.verified_at ? new Date(current.verified_at).toLocaleString() : "Not verified yet"}</Detail>
    </DetailList>
    <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2">
      <Field label="Model"><SelectControl disabled={!administrator || !current.connected || action.isPending} value={current.model} onChange={(event) => action.mutate({ name: "model", model: event.target.value })}>{current.models.map((model) => <option key={model} value={model}>{model}</option>)}</SelectControl></Field>
      <dl><Detail label="AI calls today">{current.usage.calls} / {current.usage.limit}</Detail></dl>
    </div>
    {administrator && <div className="flex flex-wrap gap-3">
      <Button disabled={action.isPending} onClick={() => action.mutate({ name: "connect" })}>{current.connected ? "Reconnect Builder.io" : "Connect Builder.io"}</Button>
      {current.connected && <Button variant="ghost" disabled={action.isPending} onClick={() => action.mutate({ name: "disconnect" })}>Disconnect</Button>}
    </div>}
    {action.isPending && <p role="status" className="text-[11px]">Updating connection…</p>}
    {action.error && <p role="alert" className="text-[11px] text-[var(--red)]">{action.error.message}</p>}
  </div>
}
