import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { PageHeader } from "../components/header"
import { Detail, DetailCard, DetailList, DetailTabs } from "../components/detail"
import { ApiOperations } from "../components/api-operations"
import { PlaybookSetup } from "../components/playbook-setup"
import { Failure, Loading } from "../components/state"
import { Provider } from "../components/provider"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Toolbar } from "../components/toolbar"
import { api } from "../lib/api"

export function ApiPlaybooksPage({ onBrowser }: { onBrowser: () => void }) {
  const query = useQueryClient()
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState("")
  const [adding, setAdding] = useState(false)
  const [versioning, setVersioning] = useState(false)
  const books = useQuery({ queryKey: ["api-playbooks"], queryFn: api.getApiPlaybooks })
  const detail = useQuery({ queryKey: ["api-playbooks", selected], queryFn: () => api.getApiPlaybook(selected), enabled: Boolean(selected) })
  const connections = useQuery({ queryKey: ["connections"], queryFn: api.getConnections })
  const book = detail.data?.playbook
  const version = detail.data?.latest_version ?? detail.data?.active_version
  const publish = useMutation({ mutationFn: () => api.publishApiPlaybook(book!.id, version!.id), onSuccess: () => query.invalidateQueries({ queryKey: ["api-playbooks"] }) })
  const archive = useMutation({ mutationFn: () => api.archiveApiPlaybook(book!.id, book!.revision), onSuccess: async () => { setSelected(""); await query.invalidateQueries({ queryKey: ["api-playbooks"] }) } })
  if (adding || versioning) return <PlaybookSetup execution="api" existing={versioning ? detail.data : undefined} onClose={() => { setAdding(false); setVersioning(false) }} onCreated={async (created) => {
    await query.invalidateQueries({ queryKey: ["api-playbooks"] })
    setSelected(created.id); setAdding(false); setVersioning(false)
  }} />
  if (books.isLoading || (selected && detail.isLoading)) return <div className="page"><Loading /></div>
  if (books.error || detail.error) return <div className="page"><Failure error={books.error ?? detail.error!} /></div>
  if (book && version) return <div className="page">
    <PageHeader eyebrow="Inventory / Playbooks" title={book.name} onBack={() => setSelected("")} actions={<>
      {version.state === "draft" ? <Button disabled={publish.isPending} onClick={() => publish.mutate()}>{publish.isPending ? "Publishing…" : "Publish"}</Button> : <Button onClick={() => setVersioning(true)}>New version</Button>}
      <Button variant="secondary" disabled={archive.isPending} onClick={() => archive.mutate()}>Archive</Button>
    </>} />
    {(publish.error ?? archive.error) && <Failure error={(publish.error ?? archive.error)!} />}
    <div className="grid gap-5 lg:grid-cols-2"><DetailCard><DetailList>
      <Detail label="Platform"><Provider value={book.platform} /></Detail><Detail label="Execution">API</Detail>
      <Detail label="API">{version.definition.http.base_url}</Detail><Detail label="Version">{version.number}</Detail>
      <Detail label="Status">{version.state === "published" ? "Published" : "Draft"}</Detail>
      <Detail label="Connections">{(connections.data ?? []).filter((item) => item.api_playbook_id === book.id).length}</Detail>
    </DetailList></DetailCard><DetailCard><ApiOperations definition={version.definition.http} /></DetailCard></div>
  </div>
  const rows = (books.data ?? []).filter((item) => `${item.name} ${item.platform}`.toLowerCase().includes(search.trim().toLowerCase()))
  return <div className="page">
    <PageHeader eyebrow="Inventory" title="Playbooks" actions={<Button onClick={() => setAdding(true)}><Plus className="size-3.5" /> Add playbook</Button>} />
    <DetailTabs items={[{ id: "browser", label: "Browser" }, { id: "api", label: "API" }]} value="api" onChange={(value) => { if (value === "browser") onBrowser() }} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search playbooks or platforms" onClear={() => setSearch("")} />
    <Table responsive><TableHeader><TableRow><TableHead>Playbook</TableHead><TableHead>Platform</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody columns={5} emptyMessage={search ? "No matching playbooks." : "No API playbooks yet."}>{rows.map((item) => <TableRow key={item.id}>
        <TableCell primary><button className="text-left hover:underline" onClick={() => setSelected(item.id)}>{item.name}</button></TableCell><TableCell label="Platform"><Provider value={item.platform} /></TableCell><TableCell label="Version">{item.latest_version}</TableCell>
        <TableCell label="Status"><Badge variant={item.active_version_id === item.latest_version_id ? "healthy" : "warning"}>{item.active_version_id === item.latest_version_id ? "Published" : "Draft"}</Badge></TableCell>
        <TableCell action><Button variant="ghost" size="sm" onClick={() => setSelected(item.id)}>View details</Button></TableCell>
      </TableRow>)}</TableBody></Table>
  </div>
}
