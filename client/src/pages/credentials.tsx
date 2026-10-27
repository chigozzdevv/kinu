import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, ChevronRight, LoaderCircle, Plus } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs } from "../components/detail"
import { ControlsFields, ControlsSummary } from "../components/controls"
import { buildControlPreferences, controlsAreValid, controlsFromDefinition, defaultControls, type ControlValues } from "../lib/controls"
import { PageHeader } from "../components/header"
import { DeleteResourceModal, ManageResourceModal } from "../components/manage"
import { Provider } from "../components/provider"
import { CredentialSetup } from "../components/setup"
import { Failure, Loading, Notice } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { ManagedCredential } from "../types"
import { api } from "../lib/api"
import { Field, formControl } from "../components/workspace"
import { connectionStatus, formatDate, providerName, titleCase } from "../lib/format"
import { ConnectionSetup } from "./connections"

type CredentialTarget = "approvals" | "rotations" | "connections" | "incidents"

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function sameControls(left: ControlValues, right: ControlValues) {
  return left.expiryDays === right.expiryDays
    && left.observationMinutes === right.observationMinutes
    && left.requireRevokeApproval === right.requireRevokeApproval
    && left.automaticTriggers.length === right.automaticTriggers.length
    && left.automaticTriggers.every((trigger) => right.automaticTriggers.includes(trigger))
    && left.exposureSources.length === right.exposureSources.length
    && left.exposureSources.every((source) => right.exposureSources.some((item) => item.connection_id === source.connection_id && item.resource === source.resource))
}

export function CredentialsPage({ initialCredentialId, initialControlVersionId, initialTab, onSelectCredential, onNavigate, onNavigateRotation }: {
  initialCredentialId?: string
  initialControlVersionId?: string
  initialTab?: string
  onSelectCredential: (credentialId: string, tab?: string, controlVersionId?: string) => void
  onNavigate: (target: CredentialTarget) => void
  onNavigateRotation: (runId: string) => void
}) {
  const queryClient = useQueryClient()
  const handledInitialCredential = useRef("")
  const [search, setSearch] = useState("")
  const [provider, setProvider] = useState("all")
  const [selected, setSelected] = useState<ManagedCredential | null>(null)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [addingControlConnection, setAddingControlConnection] = useState(false)
  const [editName, setEditName] = useState("")
  const [editControls, setEditControls] = useState<ControlValues>(defaultControls)
  const [tab, setTab] = useState("overview")
  const [viewControlVersionId, setViewControlVersionId] = useState<string | null>(initialControlVersionId ?? null)
  const [graph, runs, incidents, connections, environments, playbooks] = useQueries({
    queries: [
      { queryKey: ["graph"], queryFn: () => api.getGraph() },
      { queryKey: ["rotations"], queryFn: () => api.getRotations(), retry: false },
      { queryKey: ["incidents"], queryFn: () => api.getIncidents(), retry: false },
      { queryKey: ["connections"], queryFn: () => api.getConnections() },
      { queryKey: ["environments"], queryFn: () => api.getEnvironments() },
      { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks(), retry: false },
    ],
  })
  const credentialDetail = useQuery({ queryKey: ["credentials", selected?.id], queryFn: () => api.getCredential(selected!.id), enabled: Boolean(selected) })
  const currentSelected = credentialDetail.data ?? selected
  const currentControlVersion = useQuery({ queryKey: ["controls", currentSelected?.id, currentSelected?.control_version], queryFn: () => api.getCredentialControls(currentSelected!.id, currentSelected!.control_version), enabled: Boolean(currentSelected) })
  const displayedControlVersionId = viewControlVersionId ?? currentSelected?.control_version
  const displayedControlVersion = useQuery({ queryKey: ["controls", currentSelected?.id, displayedControlVersionId], queryFn: () => api.getCredentialControls(currentSelected!.id, displayedControlVersionId!), enabled: Boolean(currentSelected && displayedControlVersionId) })

  useEffect(() => {
    if (!initialCredentialId || !graph.data) return
    const requestedTab = initialTab ?? (initialControlVersionId ? "controls" : "overview")
    const target = `${initialCredentialId}:${requestedTab}:${initialControlVersionId ?? "current"}`
    if (handledInitialCredential.current === target) return
    const credential = graph.data.credentials.find((item) => item.id === initialCredentialId)
    if (!credential) return
    handledInitialCredential.current = target
    setSelected(credential)
    setTab(requestedTab)
    setViewControlVersionId(requestedTab === "controls" ? initialControlVersionId ?? credential.control_version : null)
  }, [graph.data, initialControlVersionId, initialCredentialId, initialTab])
  const createCredential = useMutation({
    mutationFn: api.importCredential.bind(api),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["graph"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ])
    },
  })
  const saveCredential = useMutation({
    mutationFn: async () => {
      let credential = currentSelected!
      if (editName.trim() !== credential.display_name) {
        credential = await api.updateCredential(credential.id, { expected_revision: credential.revision, display_name: editName.trim() })
      }
      if (!sameControls(editControls, controlsFromDefinition(currentControlVersion.data?.definition))) {
        const controls = buildControlPreferences(editControls)
        const changed = await api.updateCredentialControls(credential.id, {
          expected_revision: credential.revision,
          version_id: identifier("control_version"),
          controls,
        })
        credential = changed.credential
        queryClient.setQueryData(["controls", credential.id, changed.controls.id], changed.controls)
      }
      return credential
    },
    onSuccess: async (credential) => {
      queryClient.setQueryData(["credentials", credential.id], credential)
      setSelected(credential)
      setViewControlVersionId(null)
      setEditing(false)
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["graph"] }), queryClient.invalidateQueries({ queryKey: ["overview"] }), queryClient.invalidateQueries({ queryKey: ["controls"] })])
    },
  })
  const deleteCredential = useMutation({
    mutationFn: () => api.archiveCredential(currentSelected!.id, currentSelected!.revision),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["credentials", currentSelected!.id] })
      setConfirmingDelete(false)
      setSelected(null)
      onSelectCredential("")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["graph"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
        queryClient.invalidateQueries({ queryKey: ["rotations"] }),
        queryClient.invalidateQueries({ queryKey: ["incidents"] }),
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
      ])
    },
  })

  const rows = useMemo(() => {
    const credentials = graph.data?.credentials ?? []
    const term = search.trim().toLowerCase()
    return credentials.filter((item) => (provider === "all" || item.provider === provider) && (!term || `${item.display_name} ${item.id} ${item.provider}`.toLowerCase().includes(term)))
  }, [graph.data, provider, search])

  const queries = [graph, connections, environments]
  if (queries.some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = queries.find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>
  const activityAvailable = runs.isSuccess && incidents.isSuccess
  const activityNotice = (runs.error || incidents.error) && <Notice>Rotation and incident status is unavailable. You can still review your inventory.</Notice>
  const playbookNotice = playbooks.error && <Notice>Playbooks are unavailable. Browser setup cannot be completed right now.</Notice>

  function operationalState(item: ManagedCredential) {
    if (!activityAvailable) return { label: "Status unavailable", variant: "warning" as const, moving: false }
    const run = runs.data.find((entry) => entry.credential_id === item.id && !["completed", "compensated"].includes(entry.status))
    const incident = incidents.data.find((entry) => entry.credential_id === item.id && !["resolved", "dismissed"].includes(entry.status))
    const connection = connections.data!.find((entry) => entry.id === item.connection_id)
    if (run?.status === "running" || run?.status === "recovering") return { label: "Running", variant: "active" as const, moving: true }
    if (connection?.status !== "ready") return { ...connectionStatus(connection?.status), moving: false }
    if (run?.status === "paused" || incident?.status === "action-required") return { label: "Action required", variant: "warning" as const, moving: false }
    if (run || incident) return { label: "Pending", variant: "warning" as const, moving: false }
    return { label: "Active", variant: "healthy" as const, moving: false }
  }

  function actionFor(item: ManagedCredential): { label: string; target?: CredentialTarget; runId?: string } {
    const run = activityAvailable ? runs.data.find((entry) => entry.credential_id === item.id && !["completed", "compensated"].includes(entry.status)) : undefined
    const incident = activityAvailable ? incidents.data.find((entry) => entry.credential_id === item.id && !["resolved", "dismissed"].includes(entry.status)) : undefined
    const connection = connections.data!.find((entry) => entry.id === item.connection_id)
    if (run?.status === "paused" && run.stage === "approval") return { label: "Review approval", target: "approvals" }
    if (connection?.status === "setup-required" || connection?.status === "reauthentication-required") return { label: "Set up connection", target: "connections" }
    if (connection?.status === "degraded" || connection?.status === "disabled") return { label: "Review connection", target: "connections" }
    if (run) return { label: "Open rotation", target: "rotations", runId: run.id }
    if (incident) return { label: "Open incident", target: "incidents" }
    return { label: "View details" }
  }

  function performAction(item: ManagedCredential) {
    const action = actionFor(item)
    if (action.runId) onNavigateRotation(action.runId)
    else if (action.target) onNavigate(action.target)
    else {
      setSelected(item)
      setViewControlVersionId(null)
      setTab("overview")
      onSelectCredential(item.id)
    }
  }

  const selectedServices = currentSelected ? graph.data!.services.filter((service) => currentSelected.consumer_ids.includes(service.id)) : []
  const selectedConnection = currentSelected ? connections.data!.find((item) => item.id === currentSelected.connection_id) : undefined
  const selectedSecretStore = currentSelected ? connections.data!.find((item) => item.id === currentSelected.secret_store_connection_id) : undefined
  const selectedAction = currentSelected ? actionFor(currentSelected) : undefined
  const editChanged = Boolean(currentSelected && currentControlVersion.data) && (
    editName.trim() !== currentSelected!.display_name
    || !sameControls(editControls, controlsFromDefinition(currentControlVersion.data!.definition))
  )

  if (creating) return <>{playbookNotice && <div className="page pb-0">{playbookNotice}</div>}<CredentialSetup isOpen onClose={() => setCreating(false)} graph={graph.data!} connections={connections.data!} environments={environments.data!} playbooks={playbooks.data ?? []} onCreate={(input) => createCredential.mutateAsync(input)} /></>

  if (addingControlConnection) return <ConnectionSetup initialRoles={["incident"]} playbooks={playbooks.data ?? []} connections={connections.data!} onClose={() => setAddingControlConnection(false)} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} onCreated={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }); setAddingControlConnection(false) }} />

  if (currentSelected) return (
    <div className="page">
      {activityNotice}
      {playbookNotice}
      <PageHeader eyebrow="Inventory / Credentials" title={currentSelected.display_name} titlePrefix={<Provider value={currentSelected.provider} label={false} />} onBack={() => { setSelected(null); setViewControlVersionId(null); onSelectCredential("") }} actions={<>{(selectedAction?.target || selectedAction?.runId) && <Button onClick={() => performAction(currentSelected)}>{selectedAction.label}<ArrowUpRight className="size-3.5" /></Button>}<Button variant="secondary" onClick={() => { setEditName(currentSelected.display_name); setEditControls(controlsFromDefinition(currentControlVersion.data?.definition)); saveCredential.reset(); setEditing(true) }} disabled={!currentControlVersion.data}>Edit</Button><Button variant="ghost" onClick={() => { deleteCredential.reset(); setConfirmingDelete(true) }}>Delete</Button></>} />
      <DetailTabs items={[{ id: "overview", label: "Overview" }, { id: "consumers", label: "Consumers" }, { id: "controls", label: "Controls" }]} value={tab} onChange={(nextTab) => { setTab(nextTab); onSelectCredential(currentSelected.id, nextTab, nextTab === "controls" ? viewControlVersionId ?? undefined : undefined) }} />
      <DetailCard>
        {tab === "overview" && <DetailList><Detail label="Type">{titleCase(currentSelected.kind)}</Detail><Detail label="Scopes">{currentSelected.scopes.join(", ") || "None"}</Detail><Detail label="Provider ID">{currentSelected.provider_id ?? "Not recorded"}</Detail><Detail label="Consumers">{currentSelected.consumer_ids.length}</Detail><Detail label="Secret reference"><span className="mono-code break-all">{currentSelected.secret_reference}</span></Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
        {tab === "consumers" && <div className="divide-y divide-[var(--border-soft)]">{selectedServices.map((service) => <div key={service.id} className="grid gap-2 py-4 sm:grid-cols-[1fr_1.5fr]"><div className="text-[11px] font-semibold">{service.display_name}</div><div className="text-[10px] text-[var(--ink-soft)]"><div>{service.runtime_resource}</div><div className="mt-1 text-[var(--ink-muted)]">{environments.data!.find((item) => item.id === service.environment_id)?.display_name}</div></div></div>)}</div>}
        {tab === "controls" && <div className="space-y-6">
          {displayedControlVersion.data ? <ControlsSummary value={controlsFromDefinition(displayedControlVersion.data.definition)} connections={connections.data!} /> : displayedControlVersion.error ? <div className="text-[10px] text-[var(--red)]">{displayedControlVersion.error.message}</div> : <div className="text-[10px] text-[var(--ink-muted)]">Loading controls…</div>}
          <div className="border-t border-[var(--border-soft)] pt-5"><DetailList><Detail label="Connection">{selectedConnection?.display_name}</Detail><Detail label="Interface">{titleCase(selectedConnection?.interface ?? "unknown")}</Detail><Detail label="Secret">{selectedSecretStore?.display_name}</Detail><Detail label="Connection status"><Badge variant={connectionStatus(selectedConnection?.status).variant}>{connectionStatus(selectedConnection?.status).label}</Badge></Detail></DetailList></div>
        </div>}
      </DetailCard>
      <ManageResourceModal
        isOpen={editing}
        onClose={() => setEditing(false)}
        title="Edit credential"
        resourceLabel="credential"
        onSave={() => saveCredential.mutate()}
        saveDisabled={!editName.trim() || !controlsAreValid(editControls) || !editChanged}
        saving={saveCredential.isPending}
        error={saveCredential.error?.message}
      >
        <div className="space-y-6">
          <Field label="Credential name"><input className={formControl} value={editName} onChange={(event) => setEditName(event.target.value)} /></Field>
          <ControlsFields value={editControls} connections={connections.data!} onAddConnection={() => setAddingControlConnection(true)} onChange={setEditControls} />
        </div>
      </ManageResourceModal>
      <DeleteResourceModal
        isOpen={confirmingDelete}
        onClose={() => setConfirmingDelete(false)}
        resourceLabel="credential"
        retainedResourceNote="Stored secret remains in its secret store."
        dependencies={[{ label: "Services", items: selectedServices.map((item) => item.display_name) }]}
        onDelete={() => deleteCredential.mutate()}
        deleting={deleteCredential.isPending}
        error={deleteCredential.error?.message}
      />
    </div>
  )

  return (
    <div className="page">
      <PageHeader
        eyebrow="Inventory"
        title="Credentials"
        actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add credential</Button>}
      />

      {activityNotice}
      {playbookNotice}
      <Toolbar
        value={search}
        onChange={setSearch}
        placeholder="Search credentials or providers"
        onClear={() => { setSearch(""); setProvider("all") }}
        filters={[{
          label: "Provider",
          defaultValue: "all",
          value: provider,
          onChange: (event) => setProvider(event.target.value),
          children: <><option value="all">All providers</option>{[...new Set(graph.data!.credentials.map((item) => item.provider))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</>,
        }]}
      />

      <div>
        <Table>
          <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Consumer</TableHead><TableHead>Status</TableHead><TableHead>Updated</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
          <TableBody>
            {rows.map((item) => {
              const status = operationalState(item)
              const service = graph.data!.services.find((entry) => entry.id === item.consumer_ids[0])
              return (
                <TableRow key={item.id}>
                  <TableCell><button className="focus-ring flex items-center gap-3 rounded-lg text-left" onClick={() => { setSelected(item); setViewControlVersionId(null); setTab("overview"); onSelectCredential(item.id) }}><Provider value={item.provider} label={false} /><div><div className="font-medium hover:underline">{item.display_name}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{titleCase(item.kind)}</div></div></button></TableCell>
                  <TableCell><div>{service?.display_name ?? "Unmapped"}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{item.consumer_ids.length} binding{item.consumer_ids.length === 1 ? "" : "s"}</div></TableCell>
                  <TableCell><Badge variant={status.variant} className="gap-1.5">{status.moving && <LoaderCircle className="size-3 animate-spin" aria-hidden="true" />}{status.label}</Badge></TableCell>
                  <TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(item.updated_at)}</TableCell>
                  <TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={() => performAction(item)}>{actionFor(item).label}<ChevronRight className="size-3.5" /></Button></div></TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

    </div>
  )
}
