import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiOperations } from "../components/api-operations"
import { Detail, DetailCard, DetailList } from "../components/detail"
import { PageHeader } from "../components/header"
import { DeleteResourceModal } from "../components/manage"
import { PlaybookSetup } from "../components/playbook-setup"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { api } from "../lib/api"
import type { Playbook } from "../types"

export function ApiPlaybookDetailPage({ playbookId, onBack }: { playbookId: string; onBack: () => void }) {
  const query = useQueryClient()
  const [versioning, setVersioning] = useState(false)
  const [confirmingArchive, setConfirmingArchive] = useState(false)
  const detail = useQuery({ queryKey: ["api-playbooks", playbookId], queryFn: () => api.getApiPlaybook(playbookId) })
  const connections = useQuery({ queryKey: ["connections"], queryFn: api.getConnections })
  const book = detail.data?.playbook
  const version = detail.data?.latest_version ?? detail.data?.active_version
  const publish = useMutation({
    mutationFn: () => api.publishApiPlaybook(book!.id, version!.id),
    onSuccess: async () => {
      await Promise.all([query.invalidateQueries({ queryKey: ["api-playbooks"] }), query.invalidateQueries({ queryKey: ["api-playbooks", playbookId] })])
    },
  })
  const archive = useMutation({
    mutationFn: () => api.archiveApiPlaybook(book!.id, book!.revision),
    onSuccess: async () => {
      await query.invalidateQueries({ queryKey: ["api-playbooks"] })
      onBack()
    },
  })

  if (versioning && detail.data) return <PlaybookSetup execution="api" existing={detail.data} onClose={() => setVersioning(false)} onCreated={async (_created: Playbook) => {
    await Promise.all([query.invalidateQueries({ queryKey: ["api-playbooks"] }), query.invalidateQueries({ queryKey: ["api-playbooks", playbookId] })])
    setVersioning(false)
  }} />
  if (detail.isLoading || connections.isLoading) return <div className="page"><Loading /></div>
  if (detail.error || connections.error) return <div className="page"><Failure error={detail.error ?? connections.error!} /></div>
  if (!book || !version) return <div className="page"><PageHeader eyebrow="Inventory / Playbooks" title="Playbook unavailable" onBack={onBack} /></div>

  const status = version.state === "published" ? "Published" : "Draft"
  const connected = connections.data!.filter((connection) => connection.api_playbook_id === book.id).length
  return <div className="page">
    <PageHeader eyebrow="Inventory / Playbooks" title={book.name} onBack={onBack} actions={<>
      {version.state === "draft" ? <Button disabled={publish.isPending} onClick={() => publish.mutate()}>{publish.isPending ? "Publishing…" : "Publish"}</Button> : <Button onClick={() => setVersioning(true)}>New version</Button>}
      <Button variant="secondary" onClick={() => { archive.reset(); setConfirmingArchive(true) }}>Archive</Button>
    </>} />
    {(publish.error ?? archive.error) && <Failure error={(publish.error ?? archive.error)!} />}
    <DeleteResourceModal isOpen={confirmingArchive} onClose={() => setConfirmingArchive(false)} resourceLabel="playbook" description="Rotations using this playbook will stop until a replacement is attached." dependencies={[{ label: "Connections", items: connections.data!.filter((connection) => connection.api_playbook_id === book.id).map((connection) => connection.display_name) }]} onDelete={() => archive.mutate()} deleting={archive.isPending} error={archive.error?.message} />
    <div className="grid gap-5 lg:grid-cols-2"><DetailCard><DetailList>
      <Detail label="Type">API</Detail><Detail label="Platform">{book.platform}</Detail>
      <Detail label="API">{version.definition.http.base_url}</Detail><Detail label="Version">{version.number}</Detail>
      <Detail label="Status"><Badge variant={version.state === "published" ? "healthy" : "warning"}>{status}</Badge></Detail>
      <Detail label="Connections">{connected}</Detail>
    </DetailList></DetailCard><DetailCard><ApiOperations definition={version.definition.http} /></DetailCard></div>
  </div>
}
