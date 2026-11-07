import { useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { BookOpenText, ChevronRight, Plus } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailPage, DetailTabs } from "../components/detail"
import { PageHeader } from "../components/header"
import { ManageResourceModal } from "../components/manage"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, formControl } from "../components/workspace"
import type { Playbook } from "../types"
import { api, type PlaybookDetail } from "../lib/api"
import { formatDate, providerName } from "../lib/format"
import { ApiPlaybookDetailPage } from "./api-playbooks"
import { PlaybookSetup } from "../components/playbook-setup"

type PlaybookType = "computer-use" | "api"
type PlaybookRow = { type: PlaybookType; playbook: Playbook }

function playbookStatus(playbook: Playbook) {
  return playbook.active_version_id && playbook.active_version_id === playbook.latest_version_id ? "published" : "draft"
}

export function PlaybooksPage({ initialPlaybookId = "", onSelectPlaybook }: { initialPlaybookId?: string; onSelectPlaybook: (playbookId: string) => void }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [platform, setPlatform] = useState("all")
  const [status, setStatus] = useState("all")
  const [type, setType] = useState("all")
  const [selectedId, setSelectedId] = useState(initialPlaybookId)
  const [tab, setTab] = useState<"overview" | "procedure" | "connections">("overview")
  const [creating, setCreating] = useState(false)
  const [versioning, setVersioning] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState("")
  const [browserBooks, apiBooks, connections] = useQueries({ queries: [
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
    { queryKey: ["api-playbooks"], queryFn: api.getApiPlaybooks },
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
  ] })
  const close = () => { setSelectedId(""); onSelectPlaybook("") }
  const apiBook = selectedId ? apiBooks.data?.find((playbook) => playbook.id === selectedId) : undefined
  const browserBook = selectedId && !apiBook ? browserBooks.data?.find((playbook) => playbook.id === selectedId) : undefined
  const browserSelected = browserBook ?? null
  const detail = useQuery({ queryKey: ["playbooks", browserSelected?.id], queryFn: () => api.getPlaybook(browserSelected!.id), enabled: Boolean(browserSelected) })
  const currentSelected = detail.data?.playbook ?? browserSelected
  const latestVersion = detail.data?.latest_version ?? detail.data?.active_version
  const renamePlaybook = useMutation({
    mutationFn: () => api.renamePlaybook(currentSelected!.id, currentSelected!.revision, editName.trim()),
    onSuccess: async (playbook) => {
      queryClient.setQueryData(["playbooks", playbook.id], (current: PlaybookDetail | undefined) => current ? { ...current, playbook } : { playbook, active_version: null, latest_version: null })
      setEditing(false)
      await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    },
  })
  const archivePlaybook = useMutation({
    mutationFn: () => api.archivePlaybook(currentSelected!.id, currentSelected!.revision),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["playbooks", currentSelected!.id] })
      setEditing(false)
      close()
      await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    },
  })
  const publishPlaybook = useMutation({
    mutationFn: () => api.publishPlaybook(currentSelected!.id, latestVersion!.id),
    onSuccess: async () => {
      const refreshed = await api.getPlaybook(currentSelected!.id)
      queryClient.setQueryData(["playbooks", currentSelected!.id], refreshed)
      await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    },
  })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    const combined: PlaybookRow[] = [
      ...(browserBooks.data ?? []).map((playbook) => ({ type: "computer-use" as const, playbook })),
      ...(apiBooks.data ?? []).map((playbook) => ({ type: "api" as const, playbook })),
    ]
    return combined.filter(({ type: rowType, playbook }) =>
      (type === "all" || rowType === type)
      && (platform === "all" || playbook.platform === platform)
      && (status === "all" || playbookStatus(playbook) === status)
      && (!term || `${playbook.name} ${playbook.platform}`.toLowerCase().includes(term)),
    )
  }, [apiBooks.data, browserBooks.data, platform, search, status, type])

  if (apiBook) return <ApiPlaybookDetailPage playbookId={apiBook.id} onBack={close} />
  if ([browserBooks, apiBooks, connections].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [browserBooks, apiBooks, connections].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const attached = (playbook: Playbook) => connections.data!.filter((connection) => connection.playbook_id === playbook.id && connection.playbook_version_id === playbook.active_version_id)
  if (creating) return <PlaybookSetup onClose={() => setCreating(false)} onCreated={async (playbook, execution) => {
    const listKey = execution === "api" ? "api-playbooks" : "playbooks"
    queryClient.setQueryData<Playbook[]>([listKey], (current) => current ? [...current.filter((item) => item.id !== playbook.id), playbook] : [playbook])
    await queryClient.invalidateQueries({ queryKey: [listKey] })
    setSelectedId(playbook.id)
    onSelectPlaybook(playbook.id)
    setCreating(false)
  }} />
  if (versioning && detail.data) return <PlaybookSetup execution="browser" existing={detail.data} onClose={() => setVersioning(false)} onCreated={async (playbook) => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["playbooks"] }), queryClient.invalidateQueries({ queryKey: ["playbooks", playbook.id] })])
    setSelectedId(playbook.id)
    setTab("procedure")
    setVersioning(false)
  }} />

  if (currentSelected) return <DetailPage>
    <PageHeader eyebrow="Inventory / Playbooks" title={currentSelected.name} onBack={close} actions={<>{latestVersion?.state === "draft" ? <Button onClick={() => publishPlaybook.mutate()} disabled={publishPlaybook.isPending}>{publishPlaybook.isPending ? "Publishing…" : "Publish"}</Button> : <Button onClick={() => setVersioning(true)}>New version</Button>}<Button variant="secondary" onClick={() => { setEditName(currentSelected.name); renamePlaybook.reset(); archivePlaybook.reset(); setEditing(true) }}>Edit</Button></>} />
    {publishPlaybook.error && <div role="alert" className="mb-4 text-[10px] text-[var(--red)]">{publishPlaybook.error.message}</div>}
    <DetailTabs items={[{ id: "overview", label: "Overview" }, { id: "procedure", label: "Procedure" }, { id: "connections", label: "Connections" }]} value={tab} onChange={setTab} />
    <DetailCard>
      {tab === "overview" && <DetailList><Detail label="Type">Computer use</Detail><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Version">{currentSelected.latest_version}</Detail><Detail label="Status"><Badge variant={latestVersion?.state === "published" ? "healthy" : "warning"}>{latestVersion?.state === "published" ? "Published" : "Draft"}</Badge></Detail><Detail label="Actions">{latestVersion?.definition.steps.length ?? "None"}</Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
      {tab === "procedure" && <div className="space-y-1">{latestVersion?.definition.steps.map((step, index) => <div key={step.id} className="grid gap-2 py-2.5 sm:grid-cols-[32px_1fr_auto] sm:items-center"><span className="text-[10px] font-semibold text-[var(--ink-muted)]">{index + 1}</span><span className="text-[11px] font-medium">{step.objective}</span><span className="text-[9px] text-[var(--ink-muted)]">{step.stage}</span></div>)}{!latestVersion && <div className="py-3 text-[10px] text-[var(--ink-muted)]">No procedure.</div>}</div>}
      {tab === "connections" && <div className="space-y-1">{attached(currentSelected).map((connection) => <div key={connection.id} className="py-2.5 text-[11px] font-semibold">{connection.display_name}</div>)}{attached(currentSelected).length === 0 && <div className="py-3 text-[10px] text-[var(--ink-muted)]">Not attached to a Computer Use connection.</div>}</div>}
    </DetailCard>
    <ManageResourceModal isOpen={editing} onClose={() => setEditing(false)} title="Edit playbook" resourceLabel="playbook" onSave={() => renamePlaybook.mutate()} onDelete={() => archivePlaybook.mutate()} dependencies={[{ label: "Connections", items: attached(currentSelected).map((connection) => connection.display_name) }]} saveDisabled={!editName.trim() || editName.trim() === currentSelected.name} saving={renamePlaybook.isPending} deleting={archivePlaybook.isPending} error={(renamePlaybook.error ?? archivePlaybook.error)?.message}>
      <Field label="Playbook name"><input className={formControl} value={editName} onChange={(event) => setEditName(event.target.value)} /></Field>
    </ManageResourceModal>
  </DetailPage>

  return <div className="page">
    <PageHeader eyebrow="Inventory" title="Playbooks" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add playbook</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search playbooks or platforms" onClear={() => { setSearch(""); setPlatform("all"); setStatus("all"); setType("all") }} filters={[{ label: "Type", value: type, defaultValue: "all", onChange: (event) => setType(event.target.value), children: <><option value="all">All types</option><option value="computer-use">Computer use</option><option value="api">API</option></> }, { label: "Platform", value: platform, defaultValue: "all", onChange: (event) => setPlatform(event.target.value), children: <><option value="all">All platforms</option>{[...new Set([...browserBooks.data!, ...apiBooks.data!].map((item) => item.platform))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</> }, { label: "Status", value: status, defaultValue: "all", onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option><option value="published">Published</option><option value="draft">Draft</option></> }]} />
    <Table responsive><TableHeader><TableRow><TableHead>Playbook</TableHead><TableHead>Type</TableHead><TableHead>Platform</TableHead><TableHead>Connections</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody columns={7} emptyMessage={search.trim() || platform !== "all" || status !== "all" || type !== "all" ? "No matching playbooks." : "No playbooks yet."}>{rows.map(({ type: rowType, playbook }) => {
      const open = () => { setSelectedId(playbook.id); onSelectPlaybook(playbook.id); setTab("overview") }
      const published = playbookStatus(playbook) === "published"
      const count = rowType === "api" ? connections.data!.filter((connection) => connection.api_playbook_id === playbook.id).length : attached(playbook).length
      return <TableRow key={`${rowType}:${playbook.id}`}><TableCell primary><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={open}><Marker icon={BookOpenText} />{playbook.name}</button></TableCell><TableCell label="Type">{rowType === "api" ? "API" : "Computer use"}</TableCell><TableCell label="Platform"><Provider value={playbook.platform} /></TableCell><TableCell label="Connections">{count}</TableCell><TableCell label="Version">{playbook.latest_version}</TableCell><TableCell label="Status"><Badge variant={published ? "healthy" : "warning"}>{published ? "Published" : "Draft"}</Badge></TableCell><TableCell action className="pr-0"><Button className="pr-1" variant="ghost" size="sm" onClick={open}>View details <ChevronRight className="content-chevron size-3.5" /></Button></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}
