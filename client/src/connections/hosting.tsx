import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { IntegrationMark } from "../components/integration"
import { Failure, Loading, Notice } from "../components/state"
import { Button } from "../components/ui/button"
import { ConnectPage, Field, SetupPage, formControl } from "../components/workspace"
import { api } from "../lib/api"
import { authorizeConnection, type BeforeAuthorization } from "../lib/callback"
import type { HostingPlatform } from "../lib/api/hosting"
import type { Connection } from "../types"

const names = { vercel: "Vercel", railway: "Railway", render: "Render" }
const validId = (value: string | null) => value?.startsWith("connection_") ? value : ""

export function HostingSetup({ platform, connectionId, connection, onBack, onClose, onChanged, onCreated, beforeAuthorization }: {
  platform: HostingPlatform; connectionId?: string; onBack: () => void; onClose: () => void;
  connection?: Connection; onChanged: () => Promise<void>; onCreated?: (connection: Connection) => Promise<void>;
  beforeAuthorization?: BeforeAuthorization
}) {
  const [identity, setIdentity] = useState(() => connectionId ?? validId(new URLSearchParams(window.location.search).get("hosting")))
  const [selected, setSelected] = useState<string[] | null>(null)
  const [token, setToken] = useState("")
  const [reconnecting, setReconnecting] = useState(false)
  const [name, setName] = useState(names[platform])
  const [callbackError] = useState(() => new URLSearchParams(window.location.search).get("hosting"))
  useEffect(() => {
    if (!beforeAuthorization && new URLSearchParams(window.location.search).has("hosting")) window.history.replaceState({}, "", "/dashboard/connections")
  }, [beforeAuthorization])
  const projects = useQuery({ queryKey: ["host-projects", identity], queryFn: () => api.getHostProjects(identity), enabled: Boolean(identity) })
  const begin = useMutation({
    mutationFn: () => api.beginHosting(platform, name.trim(), token || undefined, reconnecting ? projects.data?.connection ?? connection : undefined),
    onSuccess: async (result) => {
      setToken("")
      if (result.authorization_url) authorizeConnection(result.authorization_url, platform, beforeAuthorization)
      else if (result.connection_id) {
        setIdentity(result.connection_id)
        setReconnecting(false)
        if (identity === result.connection_id) await projects.refetch()
        await onChanged()
      }
    },
  })
  const save = useMutation({
    mutationFn: () => api.saveHostProjects(identity, selected ?? projects.data!.selected, projects.data!.connection?.revision),
    onSuccess: async (connection) => {
      await projects.refetch()
      await onChanged()
      if (onCreated) await onCreated(connection)
      else onClose()
    },
  })
  if (!identity || reconnecting) return <ConnectPage eyebrow="Inventory / Connections" title={`${reconnecting ? "Reconnect" : "Connect"} ${names[platform]}`} onBack={onBack} onClose={onClose}
    error={begin.error?.message || (callbackError === "failed" ? "Authorization failed. Try again." : callbackError === "expired" ? "Sign in and connect again." : undefined)}
    action={<Button onClick={() => begin.mutate()} disabled={begin.isPending || !name.trim() || platform === "render" && !token.trim()}>{begin.isPending ? "Connecting…" : reconnecting ? "Reconnect" : "Connect"}</Button>}>
    <IntegrationMark kind={platform} name={reconnecting ? projects.data?.connection?.display_name ?? connection?.display_name ?? name : name}
      onNameChange={reconnecting ? undefined : setName} disabled={begin.isPending} />
    {platform === "render" && <div className="w-72 max-w-full"><Field label="API key"><input className={formControl} type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></Field></div>}
  </ConnectPage>
  if (projects.isLoading) return <div className="page"><Loading /></div>
  if (projects.error) return <div className="page"><Failure error={projects.error} /><div className="mt-4 flex gap-2">{connection && <Button onClick={() => setReconnecting(true)}>Reconnect</Button>}<Button variant="secondary" onClick={onBack}>Back</Button></div></div>
  if (!projects.data) return null
  const chosen = selected ?? projects.data.selected
  return <SetupPage eyebrow="Inventory / Connections" title={projects.data.display_name} steps={["Account", "Projects"]} current={1}
    onBack={onBack} onExit={onClose} onCancel={onClose} error={save.error?.message}
    primary={<Button onClick={() => save.mutate()} disabled={!chosen.length || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>}>
    <div className="max-w-[620px] space-y-2">
      {projects.data.connection && <div className="mb-4"><Button variant="secondary" onClick={() => setReconnecting(true)}>Reconnect</Button></div>}
      {!projects.data.projects.length && <Notice>No projects are available to this account.</Notice>}
      {projects.data.projects.map((project) => <label key={project.id} className="flex cursor-pointer items-center gap-3 rounded-xl border border-[var(--border-soft)] px-4 py-3 text-[12px]">
        <input type="checkbox" checked={chosen.includes(project.id)} onChange={(event) => setSelected(event.target.checked ? [...chosen, project.id] : chosen.filter((value) => value !== project.id))} />
        <span>{project.name}</span>
      </label>)}
    </div>
  </SetupPage>
}
