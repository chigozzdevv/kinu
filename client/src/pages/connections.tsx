import { useFeatures } from "../lib/features"
import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronRight, PlugZap, Plus } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailPage, DetailTabs } from "../components/detail"
import { PageHeader } from "../components/header"
import type { IntegrationKind } from "../components/integration"
import { ManageResourceModal } from "../components/manage"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading, Notice } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, ResourceSelect, formControl } from "../components/workspace"
import type { Connection, ConnectionRole, Playbook } from "../types"
import { api, type BrowserSetupResponse } from "../lib/api"
import { connectionCallbackIntegration } from "../lib/callback"
import { connectionAction, connectionStatus, formatDate, titleCase } from "../lib/format"
import { PlaybookSetup } from "../components/playbook-setup"
import { AwsSetup } from "../connections/aws"
import { HostingSetup } from "../connections/hosting"
import type { HostingPlatform } from "../lib/api/hosting"
import { GmailSetup } from "../connections/gmail"
import { BrowserSetupModal } from "../connections/browser-session"
import { disconnectImpact, openBrowserSetup, requiresBrowserAuthentication } from "../connections/management"
import { ConnectionSetup } from "../connections/setup"
import { useOrganisation } from "../lib/useorganisation"

function roleLabel(connection: Connection) {
  return connection.roles.map(titleCase).join(", ")
}

export function ConnectionsPage({ initialConnectionId = "", onSelectConnection }: { initialConnectionId?: string; onSelectConnection: (connectionId: string) => void }) {
  const features = useFeatures()
  const { active } = useOrganisation()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [role, setRole] = useState("all")
  const [selected, setSelected] = useState<Connection | null>(null)
  const [tab, setTab] = useState<"overview" | "access">("overview")
  const [selectedPlaybookVersion, setSelectedPlaybookVersion] = useState("")
  const [creating, setCreating] = useState(() => connectionCallbackIntegration() !== null)
  const [setupIntegration, setSetupIntegration] = useState<IntegrationKind | null>(null)
  const [editing, setEditing] = useState(false)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const [draftNotice, setDraftNotice] = useState(false)
  const [changingPlaybook, setChangingPlaybook] = useState(false)
  const [initialSelectionHandled, setInitialSelectionHandled] = useState(false)
  const [editName, setEditName] = useState("")
  const [browserSetup, setBrowserSetup] = useState<BrowserSetupResponse | null>(null)
  const [gmailSetupId, setGmailSetupId] = useState<string | null>(null)
  const [hostSetup, setHostSetup] = useState<{ id: string; platform: HostingPlatform } | null>(null)
  const [awsSetupId, setAwsSetupId] = useState<string | null>(null)
  const [connections, playbooks, graph, apiPlaybooks] = useQueries({ queries: [
    { queryKey: ["connections"], queryFn: () => api.getConnections(), refetchInterval: 10_000 },
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks(), retry: false },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
    { queryKey: ["api-playbooks"], queryFn: api.getApiPlaybooks, retry: false, enabled: selected?.interface === "api" },
  ] })
  const connectionDetail = useQuery({ queryKey: ["connections", selected?.id], queryFn: () => api.getConnection(selected!.id), enabled: Boolean(selected), refetchInterval: 10_000 })
  const currentSelected = connectionDetail.data ?? selected
  const platformPlaybooks = useMemo(() => (playbooks.data ?? []).filter((item) => item.platform === currentSelected?.platform), [playbooks.data, currentSelected?.platform])
  const browserPlaybooks = useMemo(() => platformPlaybooks.filter((item) => item.active_version_id), [platformPlaybooks])
  const browserAccess = currentSelected
    ? connections.data?.find((item) => item.interface === "browser" && item.verification_connection_id === currentSelected.id && !item.archived_at)
    : undefined
  const prepareBrowser = useMutation({
    mutationFn: (connectionId: string) => api.prepareProviderBrowser(connectionId),
    onSuccess: async (connection) => {
      queryClient.setQueryData(["connections", connection.id], connection)
      setSelected(connection)
      setTab("access")
      onSelectConnection(connection.id)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })
  const attach = useMutation({
    mutationFn: ({ connection, playbook }: { connection: Connection; playbook: Playbook }) => api.attachPlaybook(connection, playbook.id, playbook.active_version_id!),
    onSuccess: async (connection) => {
      queryClient.setQueryData(["connections", connection.id], connection)
      setSelected(connection)
      setChangingPlaybook(false)
      setDraftNotice(false)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })
  const open = useMutation({ mutationFn: openBrowserSetup })
  const updateConnection = useMutation({
    mutationFn: () => api.updateConnection(currentSelected!.id, { expected_revision: currentSelected!.revision, display_name: editName.trim() }),
    onSuccess: async (connection) => {
      queryClient.setQueryData(["connections", connection.id], connection)
      setSelected(connection)
      setEditing(false)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })
  const archiveConnection = useMutation({
    mutationFn: () => api.archiveConnection(currentSelected!.id, currentSelected!.revision),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["connections", currentSelected!.id] })
      setEditing(false)
      setSelected(null)
      onSelectConnection("")
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })

  useEffect(() => {
    if (initialSelectionHandled || !initialConnectionId || !connections.data) return
    const connection = connections.data.find((item) => item.id === initialConnectionId)
    if (connection) {
      setSelected(connection)
      setTab("overview")
    }
    setInitialSelectionHandled(true)
  }, [connections.data, initialConnectionId, initialSelectionHandled])

  useEffect(() => {
    setSelectedPlaybookVersion("")
    setChangingPlaybook(false)
    setDraftNotice(false)
  }, [currentSelected?.id])

  useEffect(() => {
    if (currentSelected?.interface !== "browser" || selectedPlaybookVersion) return
    setSelectedPlaybookVersion(browserPlaybooks[0]?.active_version_id ?? "")
  }, [currentSelected?.interface, selectedPlaybookVersion, browserPlaybooks])

  useEffect(() => {

    setBrowserSetup(null)
  }, [currentSelected?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const refresh = () => { void queryClient.invalidateQueries({ queryKey: ["connections"] }) }
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [queryClient])

  async function openBrowser(connection: Connection) {

      const setup = await open.mutateAsync(connection.id)

      setBrowserSetup(setup)

  }
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (connections.data ?? []).filter((item) => (role === "all" || item.roles.includes(role as ConnectionRole)) && (!term || `${item.display_name} ${item.platform} ${item.roles.join(" ")} ${item.interface}`.toLowerCase().includes(term)))
  }, [connections.data, role, search])
  if ([connections, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [connections, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>
  const playbookNotice = playbooks.error && <Notice>Playbooks are unavailable. Browser setup cannot be completed right now.</Notice>
  if (gmailSetupId) return <GmailSetup connectionId={gmailSetupId} connections={connections.data!} onClose={() => setGmailSetupId(null)} onBack={() => setGmailSetupId(null)} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }); await queryClient.invalidateQueries({ queryKey: ["gmail", gmailSetupId] }) }} onCreated={async (connection) => { setSelected(connection); setGmailSetupId(null) }} />
  if (hostSetup) return <HostingSetup platform={hostSetup.platform} connectionId={hostSetup.id} connection={connections.data?.find((item) => item.id === hostSetup.id)} onClose={() => setHostSetup(null)} onBack={() => setHostSetup(null)} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }); await queryClient.invalidateQueries({ queryKey: ["host-projects", hostSetup.id] }) }} onCreated={async (connection) => { setSelected(connection); setHostSetup(null); setTab("overview") }} />
  if (awsSetupId) return <AwsSetup connectionId={awsSetupId} onClose={() => setAwsSetupId(null)} onBack={() => setAwsSetupId(null)} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} onCreated={async (connection) => { setSelected(connection); setAwsSetupId(null) }} />

  if (creating) return <>{playbookNotice && <div className="page pb-0">{playbookNotice}</div>}<ConnectionSetup initialIntegration={setupIntegration} onClose={() => { setCreating(false); setSetupIntegration(null) }} playbooks={playbooks.data ?? []} connections={connections.data!} onCreated={async (connection) => { setCreating(false); setSelected(connection); setTab("overview"); onSelectConnection(connection.id) }} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} /></>

  if (creatingPlaybook && currentSelected) return <PlaybookSetup execution="browser" showIntroduction={false} initialPlatform={currentSelected.platform} onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => {
    await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    setSelectedPlaybookVersion(playbook.active_version_id ?? "")
    setDraftNotice(!playbook.active_version_id)
    setChangingPlaybook(true)
    setCreatingPlaybook(false)
  }} />

  if (currentSelected) return <DetailPage>
    {playbookNotice}
    <PageHeader eyebrow="Inventory / Connections" title={currentSelected.display_name} onBack={() => { setSelected(null); onSelectConnection("") }} actions={<>{["email", "github"].includes(currentSelected.platform) && <Button onClick={() => { setSetupIntegration(currentSelected.platform as IntegrationKind); setCreating(true) }}>{currentSelected.platform === "email" ? "Manage forwarding" : "Manage repositories"}</Button>}{["vercel", "railway", "render"].includes(currentSelected.platform) && <Button onClick={() => setHostSetup({ id: currentSelected.id, platform: currentSelected.platform as HostingPlatform })}>Manage projects</Button>}{currentSelected.platform === "gmail" && currentSelected.authorization_reference === `gmail://${currentSelected.id}` && <Button onClick={() => setGmailSetupId(currentSelected.id)}>Manage Gmail</Button>}{currentSelected.id.startsWith("aws_") && currentSelected.authorization_reference === `aws-role://${currentSelected.id}` && currentSelected.status === "setup-required" && <Button onClick={() => setAwsSetupId(currentSelected.id)} disabled={!features.data?.aws_connections}>Finish setup</Button>}{active.role === "administrator" && features.data?.browser_setup && currentSelected.platform === "resend" && currentSelected.interface === "api" && <Button variant="secondary" onClick={() => { if (browserAccess) { setSelected(browserAccess); setTab("access"); onSelectConnection(browserAccess.id) } else prepareBrowser.mutate(currentSelected.id) }} disabled={prepareBrowser.isPending}>{prepareBrowser.isPending ? "Preparing…" : browserAccess ? "Manage browser access" : "Add browser access"}</Button>}{currentSelected.interface === "browser" && currentSelected.playbook_version_id && requiresBrowserAuthentication(currentSelected) && <Button onClick={() => openBrowser(currentSelected)} disabled={open.isPending || !features.data?.browser_setup}>{open.isPending ? "Provisioning…" : "Connect"}</Button>}<Button variant="secondary" onClick={() => { setEditName(currentSelected.display_name); updateConnection.reset(); archiveConnection.reset(); setEditing(true) }}>Edit</Button></>} />
    <DetailTabs items={[{ id: "overview", label: "Overview" }, { id: "access", label: currentSelected.interface === "browser" ? "Browser access" : "Access" }]} value={tab} onChange={setTab} />
    <DetailCard>
      {tab === "overview" && <DetailList><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Roles">{roleLabel(currentSelected)}</Detail><Detail label="Interface">{titleCase(currentSelected.interface)}</Detail><Detail label="Authorization">{currentSelected.platform === "email" ? "Signed webhook" : titleCase(currentSelected.authorization)}</Detail><Detail label="Status"><Badge variant={connectionStatus(currentSelected.status).variant}>{connectionStatus(currentSelected.status).label}</Badge></Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
      {tab === "access" && (currentSelected.interface === "browser" ? <div className="max-w-[620px] space-y-5">
        <DetailList>
          <Detail label="Playbook">{(playbooks.data ?? []).find((item) => item.id === currentSelected.playbook_id)?.name ?? currentSelected.playbook_id ?? "Not attached"}</Detail>
          <Detail label="Session expires">{currentSelected.authorization_expires_at ? formatDate(currentSelected.authorization_expires_at, true) : currentSelected.authorization_reference ? "Stored session" : "Authentication required"}</Detail>
        </DetailList>
        {currentSelected.playbook_version_id && !changingPlaybook
          ? <Button variant="secondary" size="sm" onClick={() => { setSelectedPlaybookVersion(currentSelected.playbook_version_id ?? ""); setDraftNotice(false); setChangingPlaybook(true) }}>Change playbook</Button>
          : <div className="space-y-3">
            {draftNotice && <Notice>Saved as draft — an administrator can publish it from Playbooks.</Notice>}
            <ResourceSelect label="Playbook" value={selectedPlaybookVersion} onChange={setSelectedPlaybookVersion} addLabel="Add playbook" onAdd={() => { setDraftNotice(false); setCreatingPlaybook(true) }}>
              <option value="">Select playbook</option>
              {platformPlaybooks.map((item) => item.active_version_id ? <option key={item.id} value={item.active_version_id}>{item.name}</option> : <option key={item.id} value="" disabled>{item.name} (draft)</option>)}
            </ResourceSelect>
            <div className="flex gap-2">
              <Button onClick={() => { const playbook = browserPlaybooks.find((item) => item.active_version_id === selectedPlaybookVersion); if (playbook) attach.mutate({ connection: currentSelected, playbook }) }} disabled={!selectedPlaybookVersion || selectedPlaybookVersion === currentSelected.playbook_version_id || attach.isPending}>Attach playbook</Button>
              {currentSelected.playbook_version_id && <Button variant="ghost" onClick={() => { setSelectedPlaybookVersion(currentSelected.playbook_version_id ?? ""); setChangingPlaybook(false) }}>Cancel</Button>}
            </div>
          </div>}
      </div> : <DetailList>{currentSelected.api_playbook_id && <Detail label="Playbook">{(apiPlaybooks.data ?? []).find((item) => item.id === currentSelected.api_playbook_id)?.name ?? currentSelected.api_playbook_id}</Detail>}{currentSelected.api_playbook_version_id && <Detail label="Pinned version">{currentSelected.api_playbook_version_id}</Detail>}<Detail label="Allowed resources">{currentSelected.allowed_resources.join(", ")}</Detail><Detail label="Capabilities">{currentSelected.capabilities.length}</Detail><Detail label="Last validated">{currentSelected.last_validated_at ? formatDate(currentSelected.last_validated_at, true) : "Never"}</Detail></DetailList>)}
    </DetailCard>
    {(attach.error || open.error || prepareBrowser.error) && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{(attach.error ?? open.error ?? prepareBrowser.error)?.message}</div>}
    <ManageResourceModal isOpen={editing} onClose={() => setEditing(false)} title="Edit connection" resourceLabel="connection" onSave={() => updateConnection.mutate()} onDelete={() => archiveConnection.mutate()} deleteTitle="Disconnect connection?" deleteDescription={disconnectImpact(currentSelected)} deleteTriggerLabel="Disconnect" deleteActionLabel="Disconnect" deletingActionLabel="Disconnecting…" dependencies={[
      { label: "Credentials", items: graph.data!.credentials.filter((credential) => currentSelected.id === credential.connection_id || currentSelected.id === credential.secret_store_connection_id).map((credential) => credential.display_name) },
      { label: "Services", items: graph.data!.services.filter((service) => currentSelected.id === service.runtime_connection_id || service.telemetry_connection_ids.includes(currentSelected.id)).map((service) => service.display_name) },
    ]} saveDisabled={!editName.trim() || editName.trim() === currentSelected.display_name} saving={updateConnection.isPending} deleting={archiveConnection.isPending} error={(updateConnection.error ?? archiveConnection.error)?.message}>
      <Field label="Connection name"><input className={formControl} value={editName} onChange={(event) => setEditName(event.target.value)} /></Field>
    </ManageResourceModal>
    {browserSetup && <BrowserSetupModal connection={currentSelected} setup={browserSetup} onClose={() => {

      setBrowserSetup(null)
    }} onComplete={() => {

      setBrowserSetup(null)
      void queryClient.invalidateQueries({ queryKey: ["connections"] })
      void queryClient.invalidateQueries({ queryKey: ["connections", currentSelected.id] })
    }} />}
  </DetailPage>

  return <div className="page">
    <PageHeader eyebrow="Inventory" title="Connections" actions={<Button onClick={() => { setSetupIntegration(null); setCreating(true) }}><Plus className="size-3.5" /> Add connection</Button>} />
    {playbookNotice}
    <Toolbar value={search} onChange={setSearch} placeholder="Search connections or platforms" onClear={() => { setSearch(""); setRole("all") }} filters={[{ label: "Role", value: role, defaultValue: "all", onChange: (event) => setRole(event.target.value), children: <><option value="all">All roles</option>{[...new Set(connections.data!.flatMap((item) => item.roles))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
    <Table responsive><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Platform</TableHead><TableHead>Role</TableHead><TableHead>Interface</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody columns={6} emptyMessage={search.trim() || role !== "all" ? "No matching connections." : "No connections yet."}>{rows.map((connection) => {
      const status = connectionStatus(connection.status)
      const openDetails = () => { setSelected(connection); setTab("overview"); onSelectConnection(connection.id) }
      const openAction = openDetails
      return <TableRow key={connection.id}><TableCell primary><button className="focus-ring flex items-center gap-3 rounded-lg text-left font-medium hover:underline" onClick={openDetails}><Marker icon={PlugZap} />{connection.display_name}</button></TableCell><TableCell label="Platform"><Provider value={connection.platform} /></TableCell><TableCell label="Role" className="text-[var(--ink-soft)]">{roleLabel(connection)}</TableCell><TableCell label="Interface">{titleCase(connection.interface)}</TableCell><TableCell label="Status"><Badge variant={status.variant}>{status.label}</Badge></TableCell><TableCell action className="pr-0"><Button className="pr-1" variant="ghost" size="sm" onClick={openAction}>{connectionAction(connection.status)} <ChevronRight className="content-chevron size-3.5" /></Button></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}
