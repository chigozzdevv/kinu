import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Failure, Loading, Notice } from "../components/state"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"
import { Field, SelectControl, formControl } from "../components/workspace"
import { api } from "../lib/api"
import type { Connection } from "../types"
import type { HostMapping } from "../lib/api/hosting"

export function HostKeys({ connection, connections }: { connection: Connection; connections: Connection[] }) {
  const query = useQueryClient()
  const projects = useQuery({ queryKey: ["host-projects", connection.id], queryFn: () => api.getHostProjects(connection.id) })
  const [project, setProject] = useState("")
  const [provider, setProvider] = useState("")
  const [choices, setChoices] = useState<Record<string, string>>({})
  const [health, setHealth] = useState<Record<string, string>>({})
  const variables = useQuery({ queryKey: ["host-variables", connection.id, project], queryFn: () => api.getHostVariables(connection.id, project), enabled: Boolean(project) })
  const mapped = useQuery({ queryKey: ["host-mapped", connection.id], queryFn: () => api.getHostMappings(connection.id) })
  const keys = useQuery({ queryKey: ["provider-keys", provider], queryFn: () => api.getProviderKeys(provider), enabled: Boolean(provider) })
  const providers = connections.filter((item) => item.roles.includes("provider") && item.interface === "api" && item.http && item.status === "ready")
  const updateHealth = useMutation({
    mutationFn: ({ mapping, endpoint }: { mapping: HostMapping; endpoint: string }) => api.saveHostHealth(mapping.service, endpoint),
    onSuccess: async () => { setHealth({}); await Promise.all([query.invalidateQueries({ queryKey: ["host-mapped", connection.id] }), query.invalidateQueries({ queryKey: ["graph"] })]) },
  })
  const save = useMutation({
    mutationFn: () => api.importHostKeys(connection.id, project, Object.entries(choices).filter(([, value]) => value).map(([reference, key]) => ({
      variable_reference: reference, provider_connection_id: provider, provider_id: key,
      health_url: health[reference]?.trim() || null,
    }))),
    onSuccess: async () => {
      setChoices({})
      await Promise.all([query.invalidateQueries({ queryKey: ["graph"] }), query.invalidateQueries({ queryKey: ["connections"] }), query.invalidateQueries({ queryKey: ["host-mapped", connection.id] })])
    },
  })
  if (projects.isLoading || mapped.isLoading) return <Loading />
  if (projects.error || mapped.error) return <Failure error={(projects.error ?? mapped.error)!} />
  return <div className="max-w-[872px] space-y-5">
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Project"><SelectControl value={project} onChange={(event) => { setProject(event.target.value); setChoices({}); setHealth({}); save.reset() }}><option value="">Select project</option>{projects.data?.projects.filter((item) => projects.data.selected.includes(item.id)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</SelectControl></Field>
      <Field label="Provider"><SelectControl value={provider} onChange={(event) => { setProvider(event.target.value); setChoices({}); save.reset() }}><option value="">Select provider</option>{providers.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</SelectControl></Field>
    </div>
    {!providers.length && <Notice>Connect a credential provider first.</Notice>}
    {connection.platform === "vercel" && <p className="text-[11px] text-[var(--ink-muted)]">Use variables scoped to one environment.</p>}
    {variables.isLoading || keys.isLoading ? <Loading /> : variables.error || keys.error ? <Failure error={(variables.error ?? keys.error)!} /> : <div className="divide-y divide-[var(--border-soft)]">
      {variables.data?.map((variable) => {
        const mapping = mapped.data?.find((item) => item.reference === variable.reference)
        const managed = Boolean(mapping)
        return <div key={variable.reference} className="space-y-3 py-4">
          <div className="flex items-center gap-3"><span className="text-[12px] font-semibold">{variable.name}</span><span className="flex-1 text-[10px] text-[var(--ink-muted)]">{[variable.environment_name ?? variable.environment, variable.service_name].filter(Boolean).join(" / ")}</span>{managed && <Badge variant="healthy">Managed</Badge>}</div>
          {!managed && <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Credential"><SelectControl value={choices[variable.reference] ?? ""} disabled={!provider} onChange={(event) => setChoices({ ...choices, [variable.reference]: event.target.value })}><option value="">Skip</option>{keys.data?.filter((key) => !key.disabled && ["active", "enabled"].includes(key.status ?? "")).map((key) => <option key={key.provider_id} value={key.provider_id}>{key.name ?? key.provider_id}</option>)}</SelectControl></Field>
            <Field label="Health URL"><input className={formControl} type="url" value={health[variable.reference] ?? ""} placeholder="https://app.example.com/health" onChange={(event) => setHealth({ ...health, [variable.reference]: event.target.value })} /></Field>
          </div>}
          {mapping && <div className="flex max-w-[620px] items-end gap-3">
            <div className="flex-1"><Field label="Health URL"><input className={formControl} type="url" value={health[variable.reference] ?? mapping.service.endpoint ?? ""} placeholder="https://app.example.com/health" onChange={(event) => setHealth({ ...health, [variable.reference]: event.target.value })} /></Field></div>
            <Button variant="secondary" onClick={() => updateHealth.mutate({ mapping, endpoint: health[variable.reference].trim() })} disabled={updateHealth.isPending || !health[variable.reference]?.trim() || health[variable.reference]?.trim() === mapping.service.endpoint}>Save URL</Button>
          </div>}
        </div>
      })}
      {project && variables.data?.length === 0 && <Notice>No supported variables were found.</Notice>}
    </div>}
    {Object.values(choices).some(Boolean) && <p className="text-[11px] leading-5 text-[var(--ink-muted)]">Include every workload using these keys; a health URL is required for rotation.</p>}
    {save.error && <Failure error={save.error} />}
    {updateHealth.error && <Failure error={updateHealth.error} />}
    {save.isSuccess && <p className="text-[11px] text-[var(--ink-muted)]">Credentials saved.</p>}
    <Button onClick={() => save.mutate()} disabled={!provider || !Object.values(choices).some(Boolean) || save.isPending}>{save.isPending ? "Saving…" : "Add credentials"}</Button>
  </div>
}
