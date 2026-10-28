import { useFeatures } from "../lib/features"
import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronRight, PlugZap, Plus, Upload } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { IntegrationGrid, IntegrationMark, type IntegrationKind } from "../components/integration"
import { ManageResourceModal } from "../components/manage"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading, Notice } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { ConnectPage, Field, FormGrid, ResourceSelect, SetupPage, SuccessPage, formControl } from "../components/workspace"
import type { Connection, ConnectionRole, HttpProviderApi, Playbook } from "../types"
import { activeOrganisationId, ApiError, api, type BrowserSetupResponse, type CreateConnectionInput, type GitHubDiscoveryResponse, type GitHubOnboardingResponse } from "../lib/api"
import { parseProviderAdapter } from "../lib/adapter"
import { connectionCallbackIntegration } from "../lib/callback"
import { connectionAction, connectionStatus, formatDate, titleCase } from "../lib/format"
import { PlaybookSetup } from "./playbooks"
import { BrowserSetupSession, type BrowserSetupCapability } from "./browsersetup"
import { ConnectionTypes } from "../components/connection-types"
import { AwsSetup } from "./aws"
import { HostKeys } from "./host-keys"
import { HostingSetup } from "./hosting"
import type { HostingPlatform } from "../lib/api/hosting"
import { GmailSetup } from "./gmail"

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

async function waitForBrowserSetup(setup: BrowserSetupResponse): Promise<BrowserSetupResponse> {
  let session = setup.session
  const expiresAt = Date.parse(setup.expires_at)
  while (session.status === "provisioning") {
    if (!Number.isFinite(expiresAt) || Date.now() >= expiresAt) {
      throw new Error("The browser setup session expired before it became ready.")
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2000))
    session = await api.getBrowserSetup(session.id)
  }
  if (session.status !== "ready") {
    throw new Error("The browser setup did not become available. Try again.")
  }
  return { ...setup, session }
}

function browserSetupCapability(connection: Connection, setup: BrowserSetupResponse): BrowserSetupCapability {
  return {
    gatewayUrl: setup.gateway_url,
    organisationId: connection.organisation_id,
    revision: setup.session.revision,
    setupId: setup.session.id,
    token: setup.token,
    providerUrl: connection.platform.toLowerCase() === "resend" ? "https://resend.com/login" : "",
  }
}

function BrowserSetupModal({ connection, setup, onClose, onComplete }: { connection: Connection; setup: BrowserSetupResponse; onClose: () => void; onComplete: () => void }) {
  return <Modal isOpen onClose={onClose} title={`Connect ${connection.display_name}`} size="wide" showClose={false} footerStart={<span />} cancelLabel="Close">
    <BrowserSetupSession capability={browserSetupCapability(connection, setup)} onComplete={onComplete} />
  </Modal>
}

function roleLabel(connection: Connection) {
  return connection.roles.map(titleCase).join(", ")
}

function requiresBrowserAuthentication(connection: Connection) {
  if (connection.interface !== "browser") return false
  if (connection.status !== "ready" || !connection.authorization_reference) return true
  if (!connection.authorization_expires_at) return false
  const expiresAt = Date.parse(connection.authorization_expires_at)
  return !Number.isFinite(expiresAt) || expiresAt <= Date.now()
}

function disconnectImpact(connection: Connection) {
  if (connection.platform === "gmail") return "Stops Gmail monitoring and removes saved access for this workspace."
  if (connection.platform === "github") return "Removing this connection will disable GitHub security monitoring for exposed credentials."
  if (connection.interface === "browser") return `Removing this connection will stop Kinu from rotating credentials through ${connection.display_name} with Computer Use.`
  if (connection.roles.includes("provider")) return `Removing this connection will stop Kinu from creating, testing, and revoking credentials through ${connection.display_name}.`
  if (connection.roles.includes("secret-store")) return `Removing this connection will stop Kinu from securely storing and retrieving replacement credentials through ${connection.display_name}.`
  if (connection.roles.includes("runtime")) return `Removing this connection will stop Kinu from deploying and verifying credential rotations through ${connection.display_name}.`
  if (connection.roles.includes("telemetry")) return `Removing this connection will stop Kinu from verifying service health through ${connection.display_name}.`
  return "Removing this connection will disable the Kinu operations that depend on it."
}

export function ConnectionsPage({ initialConnectionId = "", onSelectConnection }: { initialConnectionId?: string; onSelectConnection: (connectionId: string) => void }) {
  const features = useFeatures()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [role, setRole] = useState("all")
  const [selected, setSelected] = useState<Connection | null>(null)
  const [tab, setTab] = useState<"overview" | "access" | "credentials">("overview")
  const [selectedPlaybookVersion, setSelectedPlaybookVersion] = useState("")
  const [creating, setCreating] = useState(() => connectionCallbackIntegration() !== null)
  const [setupIntegration, setSetupIntegration] = useState<IntegrationKind | null>(null)
  const [editing, setEditing] = useState(false)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const [initialSelectionHandled, setInitialSelectionHandled] = useState(false)
  const [editName, setEditName] = useState("")
  const [browserSetup, setBrowserSetup] = useState<BrowserSetupResponse | null>(null)
  const [gmailSetupId, setGmailSetupId] = useState<string | null>(null)
  const [hostSetup, setHostSetup] = useState<{ id: string; platform: HostingPlatform } | null>(null)
  const [awsSetupId, setAwsSetupId] = useState<string | null>(null)
  const [connections, playbooks, graph] = useQueries({ queries: [
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks(), retry: false },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })
  const connectionDetail = useQuery({ queryKey: ["connections", selected?.id], queryFn: () => api.getConnection(selected!.id), enabled: Boolean(selected) })
  const currentSelected = connectionDetail.data ?? selected
  const browserPlaybooks = (playbooks.data ?? []).filter((item) => item.platform === currentSelected?.platform && item.active_version_id)
  const attach = useMutation({
    mutationFn: ({ connection, playbook }: { connection: Connection; playbook: Playbook }) => api.attachPlaybook(connection, playbook.id, playbook.active_version_id!),
    onSuccess: async (connection) => {
      queryClient.setQueryData(["connections", connection.id], connection)
      setSelected(connection)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })
  const open = useMutation({ mutationFn: async (id: string) => waitForBrowserSetup(await api.beginBrowserSetup(id)) })
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
      setTab(["vercel", "railway", "render"].includes(connection.platform) ? "credentials" : "overview")
    }
    setInitialSelectionHandled(true)
  }, [connections.data, initialConnectionId, initialSelectionHandled])

  useEffect(() => {
    if (currentSelected?.interface !== "browser") return
    setSelectedPlaybookVersion(browserPlaybooks[0]?.active_version_id ?? "")
  }, [currentSelected?.id, currentSelected?.interface]) // eslint-disable-line react-hooks/exhaustive-deps

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
  if (hostSetup) return <HostingSetup platform={hostSetup.platform} connectionId={hostSetup.id} connection={connections.data?.find((item) => item.id === hostSetup.id)} onClose={() => setHostSetup(null)} onBack={() => setHostSetup(null)} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }); await queryClient.invalidateQueries({ queryKey: ["host-projects", hostSetup.id] }) }} onCreated={async (connection) => { setSelected(connection) }} />
  if (awsSetupId) return <AwsSetup connectionId={awsSetupId} onClose={() => setAwsSetupId(null)} onBack={() => setAwsSetupId(null)} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} onCreated={async (connection) => { setSelected(connection); setAwsSetupId(null) }} />

  if (creating) return <>{playbookNotice && <div className="page pb-0">{playbookNotice}</div>}<ConnectionSetup initialIntegration={setupIntegration} onClose={() => { setCreating(false); setSetupIntegration(null) }} playbooks={playbooks.data ?? []} connections={connections.data!} onCreated={async (connection) => { setSelected(connection); setTab(["vercel", "railway", "render"].includes(connection.platform) ? "credentials" : "overview"); onSelectConnection(connection.id) }} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} /></>

  if (creatingPlaybook && currentSelected) return <PlaybookSetup initialPlatform={currentSelected.platform} onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => {
    await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    setSelectedPlaybookVersion(playbook.active_version_id ?? "")
    setCreatingPlaybook(false)
  }} />

  if (currentSelected) return <div className="page">
    {playbookNotice}
    <PageHeader eyebrow="Inventory / Connections" title={currentSelected.display_name} onBack={() => { setSelected(null); onSelectConnection("") }} actions={<>{["vercel", "railway", "render"].includes(currentSelected.platform) && <Button onClick={() => setHostSetup({ id: currentSelected.id, platform: currentSelected.platform as HostingPlatform })}>Manage projects</Button>}{currentSelected.platform === "gmail" && currentSelected.authorization_reference === `gmail://${currentSelected.id}` && <Button onClick={() => setGmailSetupId(currentSelected.id)}>Manage Gmail</Button>}{currentSelected.id.startsWith("aws_") && currentSelected.authorization_reference === `aws-role://${currentSelected.id}` && currentSelected.status === "setup-required" && <Button onClick={() => setAwsSetupId(currentSelected.id)} disabled={!features.data?.aws_connections}>Finish setup</Button>}{currentSelected.interface === "browser" && currentSelected.playbook_version_id && requiresBrowserAuthentication(currentSelected) && <Button onClick={() => openBrowser(currentSelected)} disabled={open.isPending || !features.data?.browser_setup}>{open.isPending ? "Provisioning…" : "Connect"}</Button>}<Button variant="secondary" onClick={() => { setEditName(currentSelected.display_name); updateConnection.reset(); archiveConnection.reset(); setEditing(true) }}>Edit</Button></>} />
    <DetailTabs items={[{ id: "overview", label: "Overview" }, ...(["vercel", "railway", "render"].includes(currentSelected.platform) ? [{ id: "credentials" as const, label: "Credentials" }] : []), { id: "access", label: currentSelected.interface === "browser" ? "Browser access" : "Access" }]} value={tab} onChange={setTab} />
    <DetailCard>
      {tab === "overview" && <DetailList><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Roles">{roleLabel(currentSelected)}</Detail><Detail label="Interface">{titleCase(currentSelected.interface)}</Detail><Detail label="Authorization">{titleCase(currentSelected.authorization)}</Detail><Detail label="Status"><Badge variant={connectionStatus(currentSelected.status).variant}>{connectionStatus(currentSelected.status).label}</Badge></Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
      {tab === "credentials" && <HostKeys connection={currentSelected} connections={connections.data!} />}
      {tab === "access" && (currentSelected.interface === "browser" ? <div className="max-w-[620px] space-y-5"><DetailList><Detail label="Playbook version">{currentSelected.playbook_version_id ?? "Not attached"}</Detail><Detail label="Session expires">{currentSelected.authorization_expires_at ? formatDate(currentSelected.authorization_expires_at, true) : currentSelected.authorization_reference ? "Stored session" : "Authentication required"}</Detail></DetailList>{!currentSelected.playbook_version_id && <div className="space-y-3"><ResourceSelect label="Playbook" value={selectedPlaybookVersion} onChange={setSelectedPlaybookVersion} addLabel="Add playbook" onAdd={() => setCreatingPlaybook(true)}><option value="">Select playbook</option>{browserPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</ResourceSelect><Button onClick={() => { const playbook = browserPlaybooks.find((item) => item.active_version_id === selectedPlaybookVersion); if (playbook) attach.mutate({ connection: currentSelected, playbook }) }} disabled={!selectedPlaybookVersion || attach.isPending}>Attach playbook</Button></div>}</div> : <DetailList><Detail label="Allowed resources">{currentSelected.allowed_resources.join(", ")}</Detail><Detail label="Capabilities">{currentSelected.capabilities.length}</Detail><Detail label="Last validated">{currentSelected.last_validated_at ? formatDate(currentSelected.last_validated_at, true) : "Never"}</Detail></DetailList>)}
    </DetailCard>
    {(attach.error || open.error) && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{(attach.error ?? open.error)?.message}</div>}
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
  </div>

  return <div className="page">
    <PageHeader eyebrow="Inventory" title="Connections" actions={<Button onClick={() => { setSetupIntegration(null); setCreating(true) }}><Plus className="size-3.5" /> Add connection</Button>} />
    {playbookNotice}
    <Toolbar value={search} onChange={setSearch} placeholder="Search connections or platforms" onClear={() => { setSearch(""); setRole("all") }} filters={[{ label: "Role", value: role, defaultValue: "all", onChange: (event) => setRole(event.target.value), children: <><option value="all">All roles</option>{[...new Set(connections.data!.flatMap((item) => item.roles))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
    <Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Platform</TableHead><TableHead>Role</TableHead><TableHead>Interface</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((connection) => {
      const status = connectionStatus(connection.status)
      const openDetails = () => { setSelected(connection); setTab("overview"); onSelectConnection(connection.id) }
      const openAction = openDetails
      return <TableRow key={connection.id}><TableCell><button className="focus-ring flex items-center gap-3 rounded-lg text-left font-medium hover:underline" onClick={openDetails}><Marker icon={PlugZap} />{connection.display_name}</button></TableCell><TableCell><Provider value={connection.platform} /></TableCell><TableCell className="text-[var(--ink-soft)]">{roleLabel(connection)}</TableCell><TableCell>{titleCase(connection.interface)}</TableCell><TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell><TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={openAction}>{connectionAction(connection.status)} <ChevronRight className="size-3.5" /></Button></div></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}

type ConnectionSetupProps = {
  onClose: () => void
  playbooks: Playbook[]
  connections: Connection[]
  onChanged: () => Promise<void>
  onCreated?: (connection: Connection) => Promise<void>
  initialRoles?: ConnectionRole[]
  initialIntegration?: IntegrationKind | null
}

export function ConnectionSetup({ onClose, playbooks, connections, onChanged, onCreated, initialRoles = [], initialIntegration = null }: ConnectionSetupProps) {
  const [integration, setIntegration] = useState<IntegrationKind | null>(() => connectionCallbackIntegration() ?? initialIntegration)
  const [category, setCategory] = useState<ConnectionRole | null>(initialRoles[0] ?? null)
  const features = useFeatures()
  const integrationSettings = useQuery({ queryKey: ["integrations"], queryFn: () => api.getIntegrations(), enabled: features.data?.hosting === true })
  const configured = (id: string) => integrationSettings.data?.integrations.some((item) => item.id === id && item.configured) === true
  const available = {
    vercel: features.data?.hosting === true && configured("vercel"),
    railway: features.data?.hosting === true && configured("railway"),
    render: features.data?.hosting === true,
    github: features.data?.github === true,
    gmail: features.data?.gmail === true,
    aws: features.data?.aws_connections === true,
    "custom-api": features.data?.provider_api === true,
    "computer-use": features.data?.browser_setup === true,
  }
  const requestedProvider = category === "provider"
  const requestedCloud = category === "runtime" || category === "secret-store"
  const requestedIncident = category === "incident"
  const visible: IntegrationKind[] = requestedIncident
    ? ["gmail", "github"]
    : requestedProvider
    ? ["custom-api", "computer-use"]
    : requestedCloud
      ? category === "runtime" ? ["vercel", "railway", "render", "aws"] : ["aws"]
      : []
  const connected = {
    gmail: connections.some((item) => item.platform === "gmail" && item.status === "ready"),
    github: connections.some((item) => item.platform === "github" && item.roles.includes("incident")),
    aws: connections.some((item) => item.platform === "aws" && item.status === "ready"),
    "custom-api": connections.some((item) => item.interface === "api" && item.roles.includes("provider") && item.http !== null),
    "computer-use": connections.some((item) => item.interface === "browser"),
  }

  if (features.isLoading || integrationSettings.isLoading) return <div className="page"><Loading /></div>
  if (features.error) return <div className="page"><Failure error={features.error} /></div>
  if (!integration && !category) return <div className="page max-w-[960px]">
    <PageHeader eyebrow="Inventory / Connections" title="Add connection" onBack={onClose} />
    <ConnectionTypes onSelect={setCategory} />
  </div>
  if (integration && !available[integration]) return <div className="page"><PageHeader title="Add connection" onBack={onClose} /><Notice>This connection is not available yet.</Notice></div>

  if (integration === "vercel" || integration === "railway" || integration === "render") return <HostingSetup platform={integration} {...{ onClose, onChanged, onCreated }} onBack={() => setIntegration(null)} />
  if (integration === "custom-api") return <CustomApiSetup {...{ onClose, onChanged, onCreated, connections, playbooks }} onBack={() => setIntegration(null)} />
  if (integration === "computer-use") return <ComputerUseSetup {...{ onClose, onChanged, onCreated, connections, playbooks }} onBack={() => setIntegration(null)} />
  if (integration === "gmail") return <GmailSetup {...{ onClose, onChanged, onCreated, connections }} onBack={() => setIntegration(null)} />
  if (integration === "github") return <GitHubSetup {...{ onClose, onChanged, onCreated, connections }} onBack={() => setIntegration(null)} />
  if (integration === "aws") return <AwsSetup {...{ onClose, onChanged, onCreated }} onBack={() => setIntegration(null)} />

  return <div className="page max-w-[960px]">
    <PageHeader eyebrow="Inventory / Connections" title="Add connection" onBack={() => initialRoles.length ? onClose() : setCategory(null)} />
    {!visible.some((item) => available[item]) && <Notice>Configure an integration in Settings.</Notice>}
    <IntegrationGrid visible={visible} connected={connected} available={available} onSelect={setIntegration} />
  </div>
}

function CustomApiSetup({ onClose, onBack, onChanged, onCreated }: Omit<ConnectionSetupProps, "initialRoles"> & { onBack: () => void }) {
  const [step, setStep] = useState(0)
  const [provider, setProvider] = useState("")
  const [adapter, setAdapter] = useState<HttpProviderApi | null>(null)
  const [adapterName, setAdapterName] = useState("")
  const [adapterError, setAdapterError] = useState("")
  const [token, setToken] = useState("")
  const create = useMutation({ mutationFn: () => api.connectProvider({
    display_name: provider.trim() + " API", platform: slug(provider), http: adapter!, token,
  }) })

  async function loadAdapter(file: File | undefined) {
    if (!file) return
    try {
      setAdapter(parseProviderAdapter(await file.text()))
      setAdapterName(file.name)
      setAdapterError("")
    } catch (error) {
      setAdapter(null)
      setAdapterError(error instanceof Error ? error.message : "Invalid API definition")
    }
  }

  async function submit() {
    const connection = await create.mutateAsync()
    setToken("")
    await onChanged()
    if (onCreated) await onCreated(connection)
    else onClose()
  }

  return <SetupPage eyebrow="Inventory / Connections" title="Custom API" steps={["API", "Access", "Review"]}
    current={step} onBack={() => setStep((value) => value - 1)} onExit={onBack} onCancel={onClose}
    error={adapterError || create.error?.message}
    primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={step === 0 ? !provider.trim() || !adapter : !token}>Continue</Button>
      : <Button onClick={submit} disabled={create.isPending}>{create.isPending ? "Connecting…" : "Connect"}</Button>}>
    {step === 0 && <FormGrid>
      <Field label="Provider"><input className={formControl} value={provider} onChange={(event) => setProvider(event.target.value)} /></Field>
      <Field label="API definition"><label className="focus-ring flex h-11 cursor-pointer items-center rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px]">
        <Upload className="mr-2 size-3.5" />{adapterName || "Choose JSON definition"}
        <input className="sr-only" type="file" accept="application/json,.json" onChange={(event) => void loadAdapter(event.target.files?.[0])} />
      </label></Field>
    </FormGrid>}
    {step === 1 && <Field label="Management token"><input className={formControl} type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></Field>}
    {step === 2 && <DetailList><Detail label="Provider">{provider}</Detail><Detail label="API">{adapter?.base_url}</Detail></DetailList>}
  </SetupPage>
}

function ComputerUseSetup({ onClose, onBack, onChanged, onCreated, playbooks }: ConnectionSetupProps & { onBack: () => void }) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState(0)
  const [playbookVersion, setPlaybookVersion] = useState(playbooks.find((item) => item.active_version_id)?.active_version_id ?? "")
  const [created, setCreated] = useState<Connection | null>(null)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const [browserSetup, setBrowserSetup] = useState<BrowserSetupResponse | null>(null)
  const chosenPlaybook = playbooks.find((item) => item.active_version_id === playbookVersion)
  const playbookDetail = useQuery({ queryKey: ["playbooks", chosenPlaybook?.id], queryFn: () => api.getPlaybook(chosenPlaybook!.id), enabled: Boolean(chosenPlaybook) })
  const create = useMutation({ mutationFn: (input: CreateConnectionInput) => api.createConnection(input) })
  const open = useMutation({ mutationFn: async (id: string) => waitForBrowserSetup(await api.beginBrowserSetup(id)) })
  const definition = playbookDetail.data?.active_version?.definition

  async function submit() {
    if (!chosenPlaybook || !definition) return
    const timestamp = new Date().toISOString()
    const result = await create.mutateAsync({ connection: {
      id: identifier("conn"), organisation_id: activeOrganisationId(), platform: chosenPlaybook.platform, display_name: chosenPlaybook.name, roles: ["provider"], interface: "browser", authorization: "browser-session", authorization_reference: null,
      capabilities: ["browser.authenticate", "browser.execute", "browser.secureCapture", "provider.listCredentialMetadata", "provider.createCredential", "provider.revokeCredential", "provider.testCredential"], allowed_resources: definition.allowed_domains, http: null, playbook_id: null, playbook_version_id: null, status: "setup-required", authenticated_at: null, authorization_expires_at: null, last_validated_at: null,
      region: "global", created_at: timestamp, updated_at: timestamp, revision: 0,
    }, playbook_id: chosenPlaybook.id, playbook_version_id: playbookVersion })
    await onChanged()
    setCreated(result)
  }

  async function openBrowser() {
    if (!created) return

      const setup = await open.mutateAsync(created.id)

      setBrowserSetup(setup)

  }

  async function finish() {
    if (!created) return
    const refreshed = await api.getConnection(created.id)
    if (onCreated) await onCreated(refreshed)
    else onClose()
  }

  if (creatingPlaybook) return <PlaybookSetup onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => { await queryClient.invalidateQueries({ queryKey: ["playbooks"] }); setPlaybookVersion(playbook.active_version_id ?? ""); setCreatingPlaybook(false) }} />
  if (created) return <>
    <SuccessPage eyebrow="Inventory / Connections" title="Finish provider sign-in" onBack={finish} actions={<><Button variant="secondary" onClick={finish}>Finish later</Button><Button onClick={openBrowser} disabled={open.isPending}>{open.isPending ? "Provisioning…" : "Connect"}</Button></>}><DetailList><Detail label="Playbook">{chosenPlaybook?.name}</Detail><Detail label="Status">Authentication required</Detail></DetailList>{open.error && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{open.error.message}</div>}</SuccessPage>
    {browserSetup && <BrowserSetupModal connection={created} setup={browserSetup} onClose={() => {

      setBrowserSetup(null)
    }} onComplete={() => {

      setBrowserSetup(null)
      void finish()
    }} />}
  </>

  const canContinue = Boolean(playbookVersion && definition?.allowed_domains.length)
  return <SetupPage eyebrow="Inventory / Connections" title="Computer Use" steps={["Setup", "Review"]} current={step} onBack={() => setStep((value) => value - 1)} onExit={onBack} onCancel={onClose} error={create.error?.message || playbookDetail.error?.message} primary={step === 0 ? <Button onClick={() => setStep(1)} disabled={!canContinue}>Continue</Button> : <Button onClick={submit} disabled={create.isPending}>{create.isPending ? "Creating…" : "Create connection"}</Button>}>
    {step === 0 && <div className="space-y-5"><p className="text-[10px] leading-5 text-[var(--ink-muted)]">Computer Use follows a playbook to rotate credentials in the vendor dashboard.</p><ResourceSelect label="Playbook" value={playbookVersion} onChange={setPlaybookVersion} addLabel="Add playbook" onAdd={() => setCreatingPlaybook(true)}><option value="">Select playbook</option>{playbooks.filter((item) => item.active_version_id).map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</ResourceSelect></div>}
    {step === 1 && <Section title="Computer Use" onEdit={() => setStep(0)}><DetailList><Detail label="Playbook">{chosenPlaybook?.name}</Detail><Detail label="Platform"><Provider value={chosenPlaybook?.platform ?? ""} /></Detail><Detail label="Access">Secure browser</Detail></DetailList></Section>}
  </SetupPage>
}

type SavedGitHubOnboarding = GitHubOnboardingResponse & { installation_id?: number }

function GitHubSetup({ onClose, onBack, onChanged, onCreated, connections }: Pick<ConnectionSetupProps, "onClose" | "onChanged" | "onCreated" | "connections"> & { onBack: () => void }) {
  const [step, setStep] = useState(0)
  const [discovery, setDiscovery] = useState<GitHubDiscoveryResponse | null>(null)
  const [callbackError, setCallbackError] = useState("")
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const githubConnection = connections.find((item) => item.platform === "github" && item.roles.includes("incident"))
  const begin = useMutation({
    mutationFn: () => api.beginGitHubOnboarding(),
    onSuccess: (value) => {
      sessionStorage.setItem("kinu.github", JSON.stringify(value))
      window.location.assign(value.authorization_url)
    },
  })
  const install = useMutation({
    mutationFn: () => api.beginGitHubOnboarding(),
    onSuccess: (value) => {
      sessionStorage.setItem("kinu.github", JSON.stringify(value))
      window.location.assign(value.installation_url)
    },
  })
  const discover = useMutation({
    mutationFn: ({ saved, code, state, installationId }: { saved: SavedGitHubOnboarding; code: string; state: string; installationId?: number }) => api.discoverGitHubOnboarding(saved.session.id, { state, pkce_verifier: saved.pkce_verifier, code, installation_id: installationId }),
  })
  const complete = useMutation({ mutationFn: async () => {
    if (!discovery) throw new Error("GitHub repositories are unavailable")
    const completed = await api.completeGitHubOnboarding(discovery.session.id)
    const reference = `oauth://github/installation/${completed.installation.installation_id}`
    const existing = connections.find((item) => item.authorization_reference === reference)
    if (existing) return existing
    const timestamp = new Date().toISOString()
    return api.createConnection({ connection: {
      id: `conn_github_${completed.installation.installation_id}`,
      organisation_id: activeOrganisationId(),
      platform: "github",
      display_name: `GitHub · ${completed.installation.account_login}`,
      roles: ["incident"],
      interface: "api",
      authorization: "oauth",
      authorization_reference: reference,
      capabilities: ["incident.verifyWebhook", "incident.readFinding", "repository.resolveContext"],
      allowed_resources: completed.repositories.map((item) => item.full_name),
      http: null,
      playbook_id: null,
      playbook_version_id: null,
      status: completed.installation.ready ? "ready" : "setup-required",
      authenticated_at: timestamp,
      authorization_expires_at: null,
      last_validated_at: completed.installation.ready ? timestamp : null,
      region: "global",
      created_at: timestamp,
      updated_at: timestamp,
      revision: 0,
    } })
  } })

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search)
    const callback = parameters.has("github") || parameters.has("installation_id") || (parameters.has("code") && sessionStorage.getItem("kinu.github"))
    if (!callback) return
    const clearCallback = () => window.history.replaceState({}, "", window.location.pathname)
    const raw = sessionStorage.getItem("kinu.github")
    if (!raw) {
      clearCallback()
      setCallbackError("GitHub connection session is unavailable")
      return
    }
    try {
      const saved = JSON.parse(raw) as SavedGitHubOnboarding
      const rawInstallationId = parameters.get("installation_id") ?? saved.installation_id
      const installationId = rawInstallationId === null || rawInstallationId === undefined ? undefined : Number(rawInstallationId)
      const code = parameters.get("code")
      const state = parameters.get("state")
      if (installationId !== undefined && (!Number.isInteger(installationId) || installationId <= 0)) {
        sessionStorage.removeItem("kinu.github")
        clearCallback()
        setCallbackError("GitHub did not return an installation")
        return
      }
      if (!code) {
        if (installationId === undefined) {
          sessionStorage.removeItem("kinu.github")
          clearCallback()
          setCallbackError("GitHub authorization is incomplete")
          return
        }
        const continued = { ...saved, installation_id: installationId }
        sessionStorage.setItem("kinu.github", JSON.stringify(continued))
        clearCallback()
        window.location.assign(saved.authorization_url)
        return
      }
      if (!state) {
        sessionStorage.removeItem("kinu.github")
        clearCallback()
        setCallbackError("GitHub authorization is incomplete")
        return
      }
      sessionStorage.removeItem("kinu.github")
      clearCallback()
      discover.mutate({ saved, code, state, installationId }, {
        onSuccess: (value) => {
          setDiscovery(value)
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === "setup-required") {
            install.mutate()
          }
        },
      })
    } catch {
      sessionStorage.removeItem("kinu.github")
      clearCallback()
      setCallbackError("GitHub connection session is invalid")
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const disconnect = useMutation({
    mutationFn: () => {
      if (!githubConnection) throw new Error("GitHub connection is unavailable")
      return api.archiveConnection(githubConnection.id, githubConnection.revision)
    },
    onSuccess: async () => {
      setConfirmingDisconnect(false)
      await onChanged()
      onClose()
    },
  })

  async function submit() {
    const connection = await complete.mutateAsync()
    await onChanged()
    if (onCreated) await onCreated(connection)
    else onClose()
  }

  if (!discovery) {
    const returning = Boolean(sessionStorage.getItem("kinu.github") && connectionCallbackIntegration() === "github")
    const busy = begin.isPending || install.isPending || discover.isPending || returning
    if (githubConnection && !returning) return <><ConnectPage eyebrow="Inventory / Connections" title="GitHub" onBack={onBack} onClose={onClose} error={disconnect.error?.message} action={<Button variant="secondary" onClick={() => setConfirmingDisconnect(true)}>Disconnect</Button>}><IntegrationMark kind="github" /></ConnectPage><Modal isOpen={confirmingDisconnect} onClose={() => setConfirmingDisconnect(false)} title="Disconnect GitHub?" description={disconnectImpact(githubConnection)} size="narrow" showClose={false} actions={<Button variant="danger" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>{disconnect.isPending ? "Disconnecting…" : "Disconnect"}</Button>} /></>
    return <ConnectPage eyebrow="Inventory / Connections" title="GitHub" onBack={onBack} onClose={onClose} error={callbackError || begin.error?.message || install.error?.message || discover.error?.message} action={<Button onClick={() => begin.mutate()} disabled={busy}>{busy ? "Connecting…" : "Connect"}</Button>}><IntegrationMark kind="github" /></ConnectPage>
  }

  return <SetupPage eyebrow="Inventory / Connections" title="GitHub" steps={["Repositories", "Review"]} current={step} onBack={() => setStep(0)} onExit={onBack} onCancel={onClose} error={complete.error?.message} primary={step === 0 ? <Button onClick={() => setStep(1)}>Continue</Button> : <Button onClick={submit} disabled={complete.isPending}>{complete.isPending ? "Connecting…" : "Connect"}</Button>}>
    {step === 0 && <div className="overflow-hidden rounded-xl border border-[var(--border)]">
      <div className="grid grid-cols-[minmax(0,1fr)_140px] gap-4 border-b border-[var(--border-soft)] px-4 py-3 text-[9px] font-semibold text-[var(--ink-muted)]"><span>Repository</span><span>Status</span></div>
      {discovery.repositories.map((repository) => <div key={repository.repository_id} className="grid grid-cols-[minmax(0,1fr)_140px] items-center gap-4 border-b border-[var(--border-soft)] px-4 py-3 last:border-b-0"><div className="truncate text-[11px] font-semibold text-[var(--ink)]">{repository.full_name}</div><span className="text-[10px] text-[var(--ink-soft)]">{repository.secret_scanning === "enabled" ? "Ready" : "Action required"}</span></div>)}
    </div>}
    {step === 1 && <div className="grid gap-5 lg:grid-cols-2"><Section title="GitHub" onEdit={() => setStep(0)}><DetailList><Detail label="Account">{discovery.installation.account_login}</Detail><Detail label="Repositories">{discovery.repositories.length}</Detail></DetailList></Section><Section title="Access" onEdit={() => setStep(0)}><DetailList><Detail label="Webhook">{discovery.installation.ready ? "Verified" : "Action required"}</Detail><Detail label="Secret scanning">{discovery.repositories.every((item) => item.secret_scanning === "enabled") ? "Ready" : "Action required"}</Detail></DetailList></Section></div>}
  </SetupPage>
}
