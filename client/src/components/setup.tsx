import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { activeOrganisationId, api, type ImportCredentialInput } from "../lib/api"
import type { Connection, Environment, InventoryGraph, Playbook } from "../types"
import { finishCredentialConnection, saveCredentialReturn, type AwsKeyForm } from "../lib/credential-return"
import { ControlsFields, ControlsSummary } from "./controls"
import { buildControlPreferences, controlsAreValid, defaultControls, type ControlValues } from "../lib/controls"
import { Detail, DetailList, Section } from "./detail"
import { Provider } from "./provider"
import { Button } from "./ui/button"
import { FormGrid, ResourceSelect, SelectControl, SetupPage } from "./workspace"
import { ConnectionSetup } from "../connections/setup"

const steps = ["Credential", "Controls", "Review"]
const field = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none"

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function runtimeBindingKey(container: string | null, name: string) {
  return JSON.stringify([container, name])
}

function bindingUsesSecret(_runtimeReference: string, binding: { secret: string; version: string }, secretResource: string, secretReference: string) {
  const resource = binding.secret
  return resource === secretResource && binding.version === secretReference.split("/versions/")[1]
}

function Label({ title, message, children }: { title: string; message?: string; children: ReactNode }) {
  return <div><label className="block"><span className="mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]">{title}</span>{children}</label>{message && <span role="alert" className="mt-1.5 block text-[9px] text-[var(--red)]">{message}</span>}</div>
}

export function CredentialSetup({
  isOpen,
  runtime,
  runtimeField,
  onClose,
  graph,
  resume,
  connections,
  environments,
  playbooks,
  onCreate,
}: {
  isOpen: boolean
  runtime: Connection
  runtimeField: ReactNode
  onClose: () => void
  resume?: AwsKeyForm
  graph: InventoryGraph
  connections: Connection[]
  environments: Environment[]
  playbooks: Playbook[]
  onCreate: (input: ImportCredentialInput) => Promise<unknown>
}) {
  const queryClient = useQueryClient()
  const initialized = useRef(false)
  const [step, setStep] = useState(resume?.step ?? 0)
  const [connectionId, setConnectionId] = useState(resume?.connectionId ?? "")
  const [secretStoreId, setSecretStoreId] = useState(resume?.secretStoreId ?? "")
  const [secretResource, setSecretResource] = useState(resume?.secretResource ?? "")
  const [secretReference, setSecretReference] = useState(resume?.secretReference ?? "")
  const runtimeConnectionId = runtime.id
  const [runtimeResource, setRuntimeResource] = useState(resume?.runtimeResource ?? "")
  const [environmentName, setEnvironmentName] = useState(resume?.environmentName ?? "")
  const [controls, setControls] = useState<ControlValues>(resume?.controls ?? defaultControls)
  const [runtimeSecretName, setRuntimeSecretName] = useState(resume?.runtimeSecretName ?? "")
  const [runtimeContainerName, setRuntimeContainerName] = useState<string | null>(resume?.runtimeContainerName ?? null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [dependency, setDependency] = useState<"connection" | "secret-location" | "incident" | null>(resume ? "incident" : null)

  const managementConnections = useMemo(() => connections.filter((item) => item.roles.includes("provider") && item.status === "ready" && (
    (item.interface === "api" && item.capabilities.includes("provider.listCredentialMetadata"))
    || (item.interface === "browser" && item.playbook_id !== null && item.playbook_version_id !== null)
  )), [connections])
  const secretStores = useMemo(() => connections.filter((item) => item.platform === "aws" && item.roles.includes("secret-store") && item.interface === "api" && item.status === "ready"), [connections])
  const connection = connections.find((item) => item.id === connectionId)
  const secretStore = connections.find((item) => item.id === secretStoreId)
  const provider = connection?.platform ?? ""
  const secretResourcesQuery = useQuery({
    queryKey: ["connections", secretStoreId, "secret-resources"],
    queryFn: () => api.getSecretResources(secretStoreId),
    enabled: isOpen && Boolean(secretStoreId),
  })
  const secretVersionsQuery = useQuery({
    queryKey: ["connections", secretStoreId, "secret-versions", secretResource],
    queryFn: () => api.getSecretVersions(secretStoreId, secretResource),
    enabled: isOpen && Boolean(secretStoreId && secretResource),
  })
  const runtimeResourcesQuery = useQuery({
    queryKey: ["connections", runtimeConnectionId, "runtime-resources"],
    queryFn: () => api.getRuntimeResources(runtimeConnectionId),
    enabled: isOpen && Boolean(runtimeConnectionId),
  })
  const credentialResolutionQuery = useQuery({
    queryKey: ["credential-resolution", connectionId, secretStoreId, secretReference],
    queryFn: () => api.resolveCredential(connectionId, {
      secret_store_connection_id: secretStoreId,
      secret_reference: secretReference,
    }),
    enabled: isOpen && Boolean(connectionId && secretStoreId && secretReference),
    retry: false,
  })
  const resolvedCredential = credentialResolutionQuery.data
  const compatibleRuntimeResources = (runtimeResourcesQuery.data ?? []).filter((item) => (item.secret_bindings ?? []).some((binding) => bindingUsesSecret(item.reference, binding, secretResource, secretReference)))
  const selectedRuntimeResource = compatibleRuntimeResources.find((item) => item.reference === runtimeResource)
  const compatibleRuntimeBindings = selectedRuntimeResource?.secret_bindings?.filter((binding) => bindingUsesSecret(selectedRuntimeResource.reference, binding, secretResource, secretReference)) ?? []
  const selectedRuntimeBinding = compatibleRuntimeBindings.find((binding) => binding.name === runtimeSecretName && binding.container === runtimeContainerName)
  const existingService = graph.services.find((item) => item.runtime_connection_id === runtimeConnectionId && item.runtime_resource === runtimeResource)
  const existingEnvironment = environments.find((item) => item.id === existingService?.environment_id)
  const resolvedEnvironmentName = existingEnvironment?.display_name ?? selectedRuntimeResource?.environment_name ?? environmentName
  const selectedSecret = secretResourcesQuery.data?.find((item) => item.reference === secretResource)
  const name = resolvedCredential?.name ?? selectedSecret?.display_name ?? ""
  const providerId = connection?.interface === "browser" ? null : resolvedCredential?.provider_id ?? null
  const kind = resolvedCredential?.kind ?? ""
  const scopes = resolvedCredential?.scopes ?? []
  const alreadyImported = connection?.interface === "browser"
    ? Boolean(secretReference && graph.credentials.some((credential) => credential.connection_id === connectionId && credential.secret_reference === secretReference && !credential.archived_at))
    : Boolean(providerId && graph.credentials.some((credential) => credential.connection_id === connectionId && credential.provider_id === providerId && !credential.archived_at))
  const credentialMessage = credentialResolutionQuery.error?.message

  useEffect(() => {
    if (!isOpen) {
      initialized.current = false
      return
    }
    if (initialized.current) return
    initialized.current = true
    if (resume) return
    setStep(0)
    setConnectionId(managementConnections[0]?.id ?? "")
    setSecretStoreId(secretStores.find((item) => item.id === runtimeConnectionId)?.id ?? secretStores[0]?.id ?? "")
    setSecretResource("")
    setSecretReference("")
    setRuntimeResource("")
    setEnvironmentName("")
    setControls(defaultControls)
    setRuntimeSecretName("")
    setRuntimeContainerName(null)
    setSubmitting(false)
    setError("")
  }, [isOpen, managementConnections, runtimeConnectionId, secretStores, resume])

  useEffect(() => {
    if (!error) return
    const timeout = window.setTimeout(() => setError(""), 5000)
    return () => window.clearTimeout(timeout)
  }, [error])

  function canContinue() {
    if (step === 0) return Boolean(connectionId && secretStoreId && secretReference && resolvedCredential && (connection?.interface === "browser" || providerId) && name && kind && !alreadyImported && connection?.status === "ready" && selectedRuntimeResource && selectedRuntimeBinding && resolvedEnvironmentName)
    if (step === 1) return controlsAreValid(controls)
    return true
  }

  function next() {
    setError("")
    setStep((value) => Math.min(value + 1, steps.length - 1))
  }

  async function submit() {
    if (alreadyImported || !connection || (connection.interface !== "browser" && !providerId) || !selectedRuntimeResource || !selectedRuntimeBinding || !resolvedEnvironmentName || !controlsAreValid(controls) || !secretStoreId || !secretReference) return
    const credentialId = identifier("cred")
    const generationId = identifier("gen")
    const controlVersionId = identifier("control_version")
    const serviceId = existingService?.id ?? identifier("svc")
    const applicationId = existingService?.application_id ?? identifier("app")
    const environmentId = existingService?.environment_id ?? identifier("env")
    const createdAt = new Date().toISOString()
    const input: ImportCredentialInput = {
      credential: {
        id: credentialId,
        organisation_id: activeOrganisationId(),
        connection_id: connection.id,
        secret_store_connection_id: secretStoreId,
        secret_resource: secretResource,
        secret_reference: secretReference,
        provider: connection.platform,
        kind: kind.trim(),
        display_name: name.trim(),
        provider_id: providerId,
        scopes,
        consumer_ids: [serviceId],
        active_generation_id: generationId,
        control_version: controlVersionId,
        created_at: createdAt,
        updated_at: createdAt,
        revision: 0,
      },
      generation: {
        id: generationId,
        organisation_id: activeOrganisationId(),
        credential_id: credentialId,
        provider_id: providerId,
        fingerprint: null,
        scopes,
        state: "active",
        attempt_id: identifier("attempt"),
        secret_reference: secretReference,
        predecessor_id: null,
        successor_id: null,
        created_at: createdAt,
        revoked_at: null,
      },
      consumer: {
        application_id: applicationId,
        environment_id: environmentId,
        service_id: serviceId,
        binding_id: identifier("binding"),
        runtime_connection_id: runtimeConnectionId,
        runtime_resource: selectedRuntimeResource.reference,
        runtime_secret_name: selectedRuntimeBinding.name,
        ...(selectedRuntimeBinding.container ? { runtime_container_name: selectedRuntimeBinding.container } : {}),
        ...(!selectedRuntimeResource.environment_name && !existingEnvironment ? { environment_name: environmentName } : {}),
      },
      controls: buildControlPreferences(controls),
    }

    setSubmitting(true)
    setError("")
    try {
      await onCreate(input)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Credential could not be added")
    } finally {
      setSubmitting(false)
    }
  }

  function closeDependency() {
    finishCredentialConnection()
    setDependency(null)
  }

  if (dependency) return <ConnectionSetup
    integrations={dependency === "secret-location" ? ["aws"] : undefined}
    beforeAuthorization={(integration) => saveCredentialReturn(activeOrganisationId(), integration, {
      source: "aws", form: { step, connectionId, secretStoreId, secretResource, secretReference, runtimeConnectionId,
        runtimeResource, environmentName, controls, runtimeSecretName, runtimeContainerName },
    })}
    initialRoles={dependency === "connection" ? ["provider"] : dependency === "secret-location" ? ["secret-store"] : ["incident"]}
    playbooks={playbooks}
    connections={connections}
    onClose={closeDependency}
    onChanged={() => queryClient.invalidateQueries({ queryKey: ["connections"] })}
    onCreated={async (created) => {
      if (dependency === "connection") setConnectionId(created.id)
      else if (dependency === "secret-location") setSecretStoreId(created.id)
      closeDependency()
    }}
  />

  return (
    <SetupPage
      eyebrow="Inventory / Credentials"
      title="Add credential"
      steps={steps}
      current={step}
      onBack={() => { setError(""); setStep((value) => Math.max(0, value - 1)) }}
      onCancel={onClose}
      error={error || secretResourcesQuery.error?.message || secretVersionsQuery.error?.message || runtimeResourcesQuery.error?.message}
      primary={alreadyImported ? <Button disabled>Already added</Button> : step < steps.length - 1
        ? <Button onClick={next} disabled={!canContinue()}>Continue</Button>
        : <Button onClick={submit} disabled={submitting}>{submitting ? "Adding…" : "Add credential"}</Button>}
    >
      {step === 0 && <FormGrid>
          {runtimeField}
          <ResourceSelect label="Provider connection" value={connectionId} onChange={(value) => { setError(""); setConnectionId(value); setSecretResource(""); setSecretReference(""); setRuntimeResource(""); setRuntimeSecretName(""); setRuntimeContainerName(null) }} addLabel="Add connection" onAdd={() => setDependency("connection")} className={field}><option value="">Select connection</option>{managementConnections.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect>
          <ResourceSelect label="Secret store" value={secretStoreId} onChange={(value) => { setError(""); setSecretStoreId(value); setSecretResource(""); setSecretReference(""); setRuntimeResource(""); setRuntimeSecretName(""); setRuntimeContainerName(null) }} addLabel="Add connection" onAdd={() => setDependency("secret-location")} className={field}><option value="">Select secret store</option>{secretStores.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect>
          <Label title="Stored secret"><SelectControl className={field} value={secretResource} onChange={(event) => { setError(""); setSecretResource(event.target.value); setSecretReference(""); setRuntimeResource(""); setRuntimeSecretName(""); setRuntimeContainerName(null) }} disabled={!secretStoreId || secretResourcesQuery.isLoading}><option value="">{secretResourcesQuery.isLoading ? "Loading secrets…" : "Select secret"}</option>{secretResourcesQuery.data?.map((item) => <option key={item.reference} value={item.reference}>{item.display_name}</option>)}</SelectControl></Label>
          <Label title="Current version" message={credentialMessage}><SelectControl className={field} value={secretReference} onChange={(event) => { setError(""); setSecretReference(event.target.value); setRuntimeResource(""); setRuntimeSecretName(""); setRuntimeContainerName(null) }} disabled={!secretResource || secretVersionsQuery.isLoading}><option value="">{secretVersionsQuery.isLoading ? "Loading versions…" : "Select enabled version"}</option>{secretVersionsQuery.data?.filter((item) => item.state === "ENABLED").map((item) => <option key={item.reference} value={item.reference}>{item.reference.split("/").at(-1)}</option>)}</SelectControl></Label>
          <Label title="Runtime service"><SelectControl className={field} value={runtimeResource} onChange={(event) => { setError(""); const reference = event.target.value; setRuntimeResource(reference); setEnvironmentName(compatibleRuntimeResources.find((item) => item.reference === reference)?.environment_name ?? ""); setRuntimeSecretName(""); setRuntimeContainerName(null) }} disabled={!runtimeConnectionId || !secretReference || runtimeResourcesQuery.isLoading}><option value="">{runtimeResourcesQuery.isLoading ? "Loading services…" : compatibleRuntimeResources.length ? "Select service" : "No service uses this version"}</option>{compatibleRuntimeResources.map((item) => <option key={item.reference} value={item.reference}>{item.display_name}{item.environment_name ? ` · ${item.environment_name}` : ""}</option>)}</SelectControl></Label>
          {selectedRuntimeResource && !selectedRuntimeResource.environment_name && !existingEnvironment && <Label title="Environment"><SelectControl className={field} value={environmentName} onChange={(event) => { setError(""); setEnvironmentName(event.target.value) }}><option value="">Select environment</option><option value="Production">Production</option><option value="Staging">Staging</option></SelectControl></Label>}
          {selectedRuntimeResource && <Label title="Runtime binding"><SelectControl className={field} value={selectedRuntimeBinding ? runtimeBindingKey(selectedRuntimeBinding.container, selectedRuntimeBinding.name) : ""} onChange={(event) => { setError(""); if (!event.target.value) { setRuntimeContainerName(null); setRuntimeSecretName(""); return } const [container, variable] = JSON.parse(event.target.value) as [string | null, string]; setRuntimeContainerName(container); setRuntimeSecretName(variable) }}><option value="">Select binding</option>{compatibleRuntimeBindings.map((binding) => <option key={runtimeBindingKey(binding.container, binding.name)} value={runtimeBindingKey(binding.container, binding.name)}>{binding.name}{compatibleRuntimeBindings.length > 1 && binding.container ? ` · ${binding.container}` : ""}</option>)}</SelectControl></Label>}
        </FormGrid>}

      {step === 1 && <ControlsFields value={controls} connections={connections} onAddConnection={() => setDependency("incident")} onChange={(value) => { setError(""); setControls(value) }} />}

      {step === 2 && <div>
        <Section title="Credential" onEdit={() => setStep(0)}><DetailList><Detail label="Platform"><Provider value={provider} /></Detail><Detail label="Provider connection">{connection?.display_name}</Detail><Detail label="Secret store">{secretStore?.display_name}</Detail><Detail label="Secret">{selectedSecret?.display_name} · version {secretReference.split("/").at(-1)}</Detail></DetailList></Section>
        <Section title="Deployment" onEdit={() => setStep(0)}><DetailList><Detail label="Service">{selectedRuntimeResource?.display_name}</Detail><Detail label="Environment">{resolvedEnvironmentName}</Detail><Detail label="Runtime">{runtime.display_name}</Detail><Detail label="Binding">{selectedRuntimeBinding?.name}</Detail></DetailList></Section>
        <Section title="Controls" onEdit={() => setStep(1)}><ControlsSummary value={controls} connections={connections} /></Section>
      </div>}

    </SetupPage>
  )
}
