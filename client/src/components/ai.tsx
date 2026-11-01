import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { SettingsFooter, SettingsRow } from "./settings"
import { SelectControl, formControl } from "./workspace"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { Loading, Failure } from "./state"
import { nativeRequest, type BuilderStatus } from "../lib/native"
import { useOrganisation } from "../lib/useorganisation"
import { cn } from "../lib/utils"

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
  return <div>
    <dl className="divide-y divide-[var(--border-soft)]">
      <SettingsRow label="Builder.io"><Badge variant={current.connected ? "healthy" : "neutral"}>{current.connected ? "Connected" : "Not connected"}</Badge></SettingsRow>
      <SettingsRow label="Model" htmlFor="builder-model"><div className="w-52 max-w-full"><SelectControl id="builder-model" className={cn(formControl, "h-9 rounded-lg")} disabled={!administrator || !current.connected || action.isPending} value={current.model} onChange={(event) => action.mutate({ name: "model", model: event.target.value })}>{current.models.map((model) => <option key={model} value={model}>{model}</option>)}</SelectControl></div></SettingsRow>
      <SettingsRow label="AI calls today">{current.usage.calls} / {current.usage.limit}</SettingsRow>
      <SettingsRow label="Last verified">{current.verified_at ? new Date(current.verified_at).toLocaleString() : "Not verified yet"}</SettingsRow>
    </dl>
    {administrator && <SettingsFooter>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ name: "connect" })}>{current.connected ? "Reconnect Builder.io" : "Connect Builder.io"}</Button>
        {current.connected && <Button size="sm" variant="ghost" disabled={action.isPending} onClick={() => action.mutate({ name: "disconnect" })}>Disconnect</Button>}
      </div>
      {action.isPending && <p role="status" className="mt-4 text-[11px]">Updating connection…</p>}
      {action.error && <p role="alert" className="mt-4 text-[11px] text-[var(--red)]">{action.error.message}</p>}
    </SettingsFooter>}
  </div>
}
