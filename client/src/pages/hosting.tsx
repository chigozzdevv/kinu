import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { IntegrationMark } from "../components/integration"
import { Failure, Loading, Notice } from "../components/state"
import { Button } from "../components/ui/button"
import { ConnectPage, Field, SetupPage, formControl } from "../components/workspace"
import { api } from "../lib/api"
import type { HostingPlatform } from "../lib/api/hosting"
import type { Connection } from "../types"

const names = { vercel: "Vercel", railway: "Railway", render: "Render" }
const validId = (value: string | null) => value?.startsWith("connection_") ? value : ""

export function HostingSetup({ platform, connectionId, onBack, onClose, onChanged, onCreated }: {
  platform: HostingPlatform; connectionId?: string; onBack: () => void; onClose: () => void;
  onChanged: () => Promise<void>; onCreated?: (connection: Connection) => Promise<void>;
}) {
  const [identity, setIdentity] = useState(() => connectionId ?? validId(new URLSearchParams(window.location.search).get("hosting")))
  const [selected, setSelected] = useState<string[] | null>(null)
  const [token, setToken] = useState("")
  const [name, setName] = useState(names[platform])
  const [callbackError] = useState(() => new URLSearchParams(window.location.search).get("hosting"))
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("hosting")) window.history.replaceState({}, "", "/dashboard/connections")
  }, [])
  const projects = useQuery({ queryKey: ["host-projects", identity], queryFn: () => api.getHostProjects(identity), enabled: Boolean(identity) })
  const begin = useMutation({
    mutationFn: () => api.beginHosting(platform, name.trim(), token || undefined),
    onSuccess: (result) => {
      setToken("")
      if (result.authorization_url) window.location.assign(result.authorization_url)
      else if (result.connection_id) setIdentity(result.connection_id)
    },
  })
  const save = useMutation({
    mutationFn: () => api.saveHostProjects(identity, selected ?? projects.data!.selected, projects.data!.connection?.revision),
    onSuccess: async (connection) => {
      if (window.location.search.includes("hosting=")) window.history.replaceState({}, "", "/dashboard/connections")
      await onChanged()
      await onCreated?.(connection)
      onClose()
    },
  })
  if (!identity) return <ConnectPage eyebrow="Inventory / Connections" title={`Connect ${names[platform]}`} onBack={onBack} onClose={onClose}
    error={begin.error?.message || (callbackError === "failed" ? "Authorization failed. Try again." : callbackError === "expired" ? "Sign in and connect again." : undefined)}
    action={<Button onClick={() => begin.mutate()} disabled={begin.isPending || !name.trim() || platform === "render" && !token.trim()}>{begin.isPending ? "Connecting…" : `Connect ${names[platform]}`}</Button>}>
    <IntegrationMark kind={platform} />
    <div className="mt-6 w-full space-y-4">
      <Field label="Connection name"><input className={formControl} value={name} maxLength={100} onChange={(event) => setName(event.target.value)} /></Field>
      {platform === "render" && <Field label="API key"><input className={formControl} type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></Field>}
    </div>
  </ConnectPage>
  if (projects.isLoading) return <div className="page"><Loading /></div>
  if (projects.error) return <div className="page"><Failure error={projects.error} /><Button variant="secondary" className="mt-4" onClick={onBack}>Back</Button></div>
  if (!projects.data) return null
  const chosen = selected ?? projects.data.selected
  return <SetupPage eyebrow="Inventory / Connections" title={projects.data.display_name} steps={["Account", "Projects"]} current={1}
    onBack={onBack} onExit={onClose} onCancel={onClose} error={save.error?.message}
    primary={<Button onClick={() => save.mutate()} disabled={!chosen.length || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>}>
    <div className="max-w-[620px] space-y-2">
      {!projects.data.projects.length && <Notice>No projects are available to this account.</Notice>}
      {projects.data.projects.map((project) => <label key={project.id} className="flex cursor-pointer items-center gap-3 rounded-xl border border-[var(--border-soft)] px-4 py-3 text-[12px]">
        <input type="checkbox" checked={chosen.includes(project.id)} onChange={(event) => setSelected(event.target.checked ? [...chosen, project.id] : chosen.filter((value) => value !== project.id))} />
        <span>{project.name}</span>
      </label>)}
    </div>
  </SetupPage>
}
