import { useEffect, useRef, useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ControlsFields, ControlsSummary } from "../components/controls"
import { Detail, DetailList, Section } from "../components/detail"
import { Notice, RetryNotice } from "../components/state"
import { Button } from "../components/ui/button"
import { Field, FormGrid, ResourceSelect, SelectControl, SetupPage, formControl } from "../components/workspace"
import { activeOrganisationId, api } from "../lib/api"
import { buildControlPreferences, controlsAreValid, defaultControls } from "../lib/controls"
import { finishCredentialConnection, saveCredentialReturn, type HostKeyForm } from "../lib/credential-return"
import { titleCase } from "../lib/format"
import { approvedProjects, validHealthUrl, variableLabel } from "../lib/hosting"
import type { Connection, ManagedCredential, Playbook } from "../types"
import { ConnectionSetup } from "../connections/setup"

const emptyForm: HostKeyForm = {
  project: "", variable: "", provider: "", choices: {}, health: {}, createReference: "", scopes: "", step: 0,
  controls: { ...defaultControls, automaticTriggers: ["expiry"], expiryDays: 1 },
}

export function HostKeys({ connection, connections, credentials, playbooks, runtimeField, resume, onFinish }: {
  connection: Connection
  connections: Connection[]
  credentials: ManagedCredential[]
  playbooks: Playbook[]
  resume?: HostKeyForm
  runtimeField: ReactNode
  onFinish: (credentialId: string) => void
}) {
  const query = useQueryClient()
  const [form, setForm] = useState<HostKeyForm>(resume ?? emptyForm)
  const { project, provider, choices, health, controls, createReference, step } = form
  const [dependency, setDependency] = useState<"provider" | "incident" | null>(resume ? "incident" : null)
  const [tracking, setTracking] = useState("")
  const attempt = useRef({ input: "", id: "", setupId: "" })
  const mapped = useQuery({ queryKey: ["host-mapped", connection.id], queryFn: () => api.getHostMappings(connection.id) })
  const approved = approvedProjects(connection, mapped.data ?? [])
  const missingProjectNames = approved.some((item) => !item.name)
  const projects = useQuery({
    queryKey: ["host-projects", connection.id], queryFn: () => api.getHostProjects(connection.id),
    enabled: !mapped.isPending && missingProjectNames,
  })
  const eligibleProjects = approved.map((item) => ({
    id: item.id, name: projects.data?.projects.find((live) => live.id === item.id)?.name ?? item.name ?? item.id,
  }))
  const variables = useQuery({ queryKey: ["host-variables", connection.id, project], queryFn: () => api.getHostVariables(connection.id, project), enabled: Boolean(project) })
  const setups = useQuery({
    queryKey: ["key-setups", connection.id], queryFn: () => api.getKeySetups(connection.id),
    enabled: Boolean(project),
    refetchInterval: (result) => result.state.data?.some((item) => !["completed", "cancelled"].includes(item.phase)) ? 3_000 : false,
  })
  const providers = connections.filter((item) => item.status === "ready" && item.roles.includes("provider")
    && ((item.interface === "api" && item.http) || item.interface === "browser"))
  const selectedProvider = providers.find((item) => item.id === provider)
  const fixedScopes = selectedProvider?.interface === "api" ? selectedProvider.http?.credential_scopes ?? null : null
  const keys = useQuery({ queryKey: ["provider-keys", provider], queryFn: () => api.getProviderKeys(provider), enabled: Boolean(selectedProvider) })
  const availableKeys = keys.data?.filter((key) => !key.disabled && ["active", "enabled"].includes(key.status ?? "")) ?? []
  const projectName = eligibleProjects.find((item) => item.id === project)?.name
  const registeredVariables = (mapped.data ?? []).flatMap((entry) => entry.variable?.project_id === project ? [entry.variable] : [])
  const availableVariables = new Map([...registeredVariables, ...(variables.data ?? [])].map((item) => [item.reference, item]))
  const variable = availableVariables.get(form.variable)
  const mapping = variable && mapped.data?.find((item) => item.reference === variable.reference)
  const setup = variable && setups.data?.find((item) => item.reference === variable.reference)
  const settingUp = setup && setup.phase !== "cancelled" && setup.phase !== "completed"
  const mappings = Object.entries(choices).filter(([, key]) => key)
  const newKey = createReference !== ""
  const newVariables = variables.data?.filter((item) => newKey ? item.reference === createReference : choices[item.reference]) ?? []
  const hasNewCredentials = newKey || !mappings.length || mappings.some(([, key]) =>
    !credentials.some((item) => item.connection_id === provider && item.provider_id === key && !item.archived_at))
  const steps = hasNewCredentials ? ["Credential", "Controls", "Review"] : ["Credential", "Review"]
  const otherChoices = mappings.some(([reference]) => reference !== form.variable)
  const canCreate = variable?.readable && selectedProvider?.interface === "api" && selectedProvider.http?.supports_overlap && (!setup || setup.phase === "cancelled")
  const selectionsValid = newKey
    ? canCreate && newVariables.length === 1 && validHealthUrl(health[createReference] ?? "")
    : mappings.length > 0 && newVariables.length === mappings.length && mappings.every(([ref, key]) =>
        availableKeys.some((item) => item.provider_id === key) && (!health[ref]?.trim() || validHealthUrl(health[ref])))
  const unclaimed = newVariables.every((item) => !mapped.data?.some((entry) => entry.reference === item.reference)
    && !setups.data?.some((entry) => entry.reference === item.reference && !["completed", "cancelled"].includes(entry.phase)))
  const canContinue = Boolean(!mapping && projectName && selectedProvider && mapped.isSuccess && setups.isSuccess && variables.isSuccess && selectionsValid && unclaimed)

  function update(patch: Partial<HostKeyForm>) {
    setForm((current) => ({ ...current, ...patch }))
  }
  function chooseProvider(id: string) {
    if (id !== provider) update({ provider: id, choices: {}, createReference: "", scopes: "" })
  }
  function closeDependency() {
    finishCredentialConnection()
    setDependency(null)
  }

  const save = useMutation({
    mutationFn: async () => {
      if (!canContinue || (hasNewCredentials && !controlsAreValid(controls))) throw new Error("Review the credential selections before saving.")
      const preferences = hasNewCredentials ? buildControlPreferences(controls) : undefined
      if (newKey) {
        const input = {
          project_id: project, variable_reference: createReference, provider_connection_id: provider,
          health_url: health[createReference].trim(), scopes: form.scopes.split(/[\s,]+/).filter(Boolean),
          initial_controls: buildControlPreferences(controls),
        }
        const signature = JSON.stringify(input)
        if (attempt.current.input !== signature) attempt.current = { input: signature, id: crypto.randomUUID(), setupId: "" }
        const pending = await api.createManagedKey(connection.id, attempt.current.id, input)
        attempt.current.setupId = pending.id
        setTracking(pending.id)
        update({ step: 0, createReference: "" })
        await query.invalidateQueries({ queryKey: ["key-setups", connection.id] })
        return
      }
      const saved = await api.importHostKeys(connection.id, project, mappings.map(([reference, key]) => ({
        variable_reference: reference, provider_connection_id: provider, provider_id: key, health_url: health[reference]?.trim() || null,
      })), preferences)
      await Promise.all(["graph", "overview", "host-mapped"].map((key) => query.invalidateQueries({ queryKey: [key] })))
      onFinish(saved.length === 1 ? saved[0].id : "")
    },
  })
  const recover = useMutation({
    mutationFn: (id: string) => api.recoverKeySetup(connection.id, id),
    onSuccess: async (result) => { setTracking(result.id); await query.invalidateQueries({ queryKey: ["key-setups", connection.id] }) },
  })
  const completed = setups.data?.find((item) => item.id === tracking && item.phase === "completed")
  useEffect(() => {
    if (!completed?.credential_id) return
    const id = completed.credential_id
    let active = true
    void Promise.all(["graph", "overview", "host-mapped", "provider-keys"].map((key) => query.invalidateQueries({ queryKey: [key] })))
      .then(() => { if (active) onFinish(id) })
    return () => { active = false }
  }, [completed, query, onFinish])
  const completedCount = setups.data?.filter((item) => item.phase === "completed").length ?? 0
  useEffect(() => {
    if (completedCount) void query.invalidateQueries({ queryKey: ["host-mapped", connection.id] })
  }, [completedCount, connection.id, query])

  if (dependency) return <ConnectionSetup initialRoles={[dependency]} connections={connections} playbooks={playbooks} onClose={closeDependency}
    beforeAuthorization={(integration) => saveCredentialReturn(activeOrganisationId(), integration, { source: "hosting", hostId: connection.id, form })}
    onChanged={() => query.invalidateQueries({ queryKey: ["connections"] })}
    onCreated={async (created) => {
      if (dependency === "provider" && created.status === "ready") chooseProvider(created.id)
      closeDependency()
    }} />

  function chooseKey(value: string) {
    if (!variable) return
    if (value === "create" && setup?.phase === "cancelled" && setup.id === attempt.current.setupId) {
      attempt.current = { input: "", id: "", setupId: "" }
    }
    const next = { ...choices }
    if (value.startsWith("key:")) next[variable.reference] = value.slice(4)
    else delete next[variable.reference]
    update({ choices: next, createReference: value === "create" ? variable.reference : "", scopes: "" })
  }
  function goTo(next: number) {
    save.reset()
    update({ step: next })
  }
  const endpoint = variable ? health[variable.reference] ?? "" : ""
  const primary = mapping ? <Button disabled>Already added</Button> : step === 2
    ? <Button disabled={save.isPending || !canContinue || (hasNewCredentials && !controlsAreValid(controls))} onClick={() => save.mutate()}>
        {save.isPending ? "Saving…" : newKey ? "Create and install" : mappings.length > 1 ? "Link keys" : "Link key"}
      </Button>
    : <Button disabled={step === 0 ? !canContinue : !controlsAreValid(controls)} onClick={() => goTo(step === 0 && hasNewCredentials ? 1 : 2)}>Continue</Button>

  return <SetupPage eyebrow="Inventory / Credentials" title="Add credential" steps={steps} current={step === 2 ? steps.length - 1 : step}
    onExit={step === 0 ? () => onFinish("") : () => goTo(step === 2 && hasNewCredentials ? 1 : 0)}
    onBack={() => goTo(step === 2 && hasNewCredentials ? 1 : 0)} onCancel={() => onFinish("")} primary={primary} error={save.error?.message ?? recover.error?.message}>
    {step === 0 && <div className="space-y-5">
      <FormGrid>
        {runtimeField}
        <Field label="Project"><SelectControl value={project} disabled={!eligibleProjects.length} onChange={(event) => { save.reset(); setTracking(""); setForm({ ...emptyForm, project: event.target.value }) }}>
          <option value="">Select project</option>{eligibleProjects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </SelectControl></Field>
        <Field label="Variable"><SelectControl value={form.variable} disabled={!project || !availableVariables.size} onChange={(event) => update({ variable: event.target.value, createReference: "", scopes: "" })}>
          <option value="">Select variable</option>{[...availableVariables.values()].map((item) => <option key={item.reference} value={item.reference}>
            {variableLabel(item)}
          </option>)}
        </SelectControl></Field>
      </FormGrid>
      {connection.platform === "vercel" && <p className="text-[11px] text-[var(--ink-muted)]">Variables that include Production are supported.</p>}
      {projects.isError && missingProjectNames && !project && <RetryNotice onRetry={() => projects.refetch()}>Couldn’t load project names from {connection.display_name}.</RetryNotice>}
      {variables.isError && !mapping && <RetryNotice onRetry={() => variables.refetch()}>Couldn’t load variables from {connection.display_name}.</RetryNotice>}
      {mapped.isError && <RetryNotice onRetry={() => mapped.refetch()}>Couldn’t check which variables have already been added.</RetryNotice>}
      {setups.isError && <RetryNotice onRetry={() => setups.refetch()}>Couldn’t load key setup status.</RetryNotice>}
      {!eligibleProjects.length && <Notice>No projects are approved for this connection.</Notice>}
      {project && variables.isSuccess && !availableVariables.size && <Notice>No supported variables were found.</Notice>}
      {variable && mapped.isSuccess && setups.isSuccess && !mapping && !settingUp && <FormGrid>
        <ResourceSelect label="Provider" value={provider} onChange={chooseProvider} addLabel="Connect provider" onAdd={() => setDependency("provider")}>
          <option value="">Select provider</option>{providers.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
        </ResourceSelect>
        <Field label="Key"><SelectControl value={newKey ? "create" : choices[variable.reference] ? "key:" + choices[variable.reference] : ""} disabled={!selectedProvider || keys.isLoading} onChange={(event) => chooseKey(event.target.value)}>
          <option value="">Select key</option>{availableKeys.map((item) => <option key={item.provider_id} value={"key:" + item.provider_id}>{item.name ?? item.provider_id}</option>)}
          {canCreate && !otherChoices && <option value="create">Create managed key…</option>}
        </SelectControl></Field>
        <Field label={newKey ? "Health URL (required)" : "Health URL"} wide><input className={formControl} type="url" required={newKey} value={endpoint} placeholder="https://app.example.com/health" onChange={(event) => update({ health: { ...health, [variable.reference]: event.target.value } })} /></Field>
        {newKey && selectedProvider?.interface === "api" && fixedScopes === null && !(selectedProvider.platform === "resend" && !selectedProvider.api_playbook_id) && <Field label="Scopes" hint="Separate permissions with spaces or commas." wide><input className={formControl} value={form.scopes} onChange={(event) => update({ scopes: event.target.value })} /></Field>}
      </FormGrid>}
      {setup && !mapping && <div className="space-y-2 text-[11px] text-[var(--ink-soft)]" role="status">
        <p>{titleCase(setup.phase)}</p>{setup.message && <p>{setup.message}</p>}
        {setup.phase === "review-required" && <Button variant="secondary" disabled={recover.isPending} onClick={() => recover.mutate(setup.id)}>Check and recover</Button>}
      </div>}
      {mappings.length > 1 && <p className="text-[11px] text-[var(--ink-soft)]">{mappings.length} variables selected</p>}
      {!mapping && keys.isError && <RetryNotice onRetry={() => keys.refetch()}>Couldn’t load provider keys.</RetryNotice>}
    </div>}
    {step === 1 && <ControlsFields value={controls} connections={connections} onChange={(value) => update({ controls: value })} onAddConnection={() => setDependency("incident")} />}
    {step === 2 && <div>
      <Section title="Credential" onEdit={() => goTo(0)}><DetailList>
        <Detail label="Runtime connection">{connection.display_name}</Detail><Detail label="Project">{projectName}</Detail><Detail label="Provider">{selectedProvider?.display_name}</Detail>
        {newVariables.map((item) => <Detail key={item.reference} label={variableLabel(item)}>
          {newKey ? "Create and install a new key" : availableKeys.find((key) => key.provider_id === choices[item.reference])?.name ?? choices[item.reference]}
          {health[item.reference]?.trim() && <div className="mt-1">{health[item.reference].trim()}</div>}
        </Detail>)}
        {newKey && selectedProvider?.platform === "resend" && selectedProvider.interface === "api" && !selectedProvider.api_playbook_id && <Detail label="Permissions">Sending only</Detail>}
        {newKey && selectedProvider?.interface === "api" && !(selectedProvider.platform === "resend" && !selectedProvider.api_playbook_id) && (fixedScopes !== null || form.scopes.trim()) && <Detail label="Permissions">{fixedScopes !== null ? (fixedScopes.join(", ") || "None") : form.scopes.trim()}</Detail>}
        {!hasNewCredentials && <Detail label="Rotation controls">Use the credential’s existing rules</Detail>}
      </DetailList></Section>
      {newKey && <p className="mb-4 text-[11px] leading-5 text-[var(--ink-soft)]">Kinu will deploy and verify the new key. The previous provider key stays active for you to review.</p>}
      {!newKey && newVariables.some((item) => !health[item.reference]?.trim()) && <Notice>A health URL is required before this workload can rotate.</Notice>}
      {hasNewCredentials && <Section title="Rotation controls for new keys" onEdit={() => goTo(1)}><ControlsSummary value={controls} connections={connections} /></Section>}
    </div>}
  </SetupPage>
}
