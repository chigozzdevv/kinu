import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { PageSection } from "../components/header"
import { CredentialSetup } from "../components/setup"
import { RetryNotice } from "../components/state"
import { Button } from "../components/ui/button"
import { FormGrid, ResourceSelect, SetupPage } from "../components/workspace"
import { activeOrganisationId, api, type ImportCredentialInput } from "../lib/api"
import { connectionCallbackIntegration } from "../lib/callback"
import { credentialReturn, finishCredentialConnection, saveCredentialReturn } from "../lib/credential-return"
import { isHostingPlatform } from "../lib/hosting"
import { ConnectionSetup } from "./connections"
import { HostKeys } from "./host-keys"

export function CredentialOnboarding({ onFinish }: { onFinish: (credentialId: string) => void }) {
  const query = useQueryClient()
  const organisation = activeOrganisationId()
  const [resume, setResume] = useState(() => credentialReturn(organisation, connectionCallbackIntegration()))
  const [runtimeId, setRuntimeId] = useState(resume?.source === "aws" ? resume.form.runtimeConnectionId : resume?.hostId ?? "")
  const [connecting, setConnecting] = useState(resume?.source === "hosting" && !resume.hostId)
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api.getConnections() })
  const accounts = connections.data ?? []
  const runtimes = accounts.filter((item) => item.status === "ready" && !item.archived_at
    && item.roles.includes("runtime") && (isHostingPlatform(item.platform) || item.platform === "aws"))
  const runtime = runtimes.find((item) => item.id === runtimeId)
  const graph = useQuery({ queryKey: ["graph"], queryFn: () => api.getGraph(), enabled: Boolean(runtime) })
  const environments = useQuery({ queryKey: ["environments"], queryFn: () => api.getEnvironments(), enabled: runtime?.platform === "aws" })
  const playbooks = useQuery({ queryKey: ["playbooks"], queryFn: () => api.getPlaybooks(), enabled: Boolean(runtime || connecting) })

  function closeConnection() {
    finishCredentialConnection()
    setConnecting(false)
    setResume(null)
  }

  async function importAws(input: ImportCredentialInput) {
    const result = await api.importCredential(input)
    await Promise.all([query.invalidateQueries({ queryKey: ["graph"] }), query.invalidateQueries({ queryKey: ["overview"] })])
    onFinish(result.id)
  }

  const runtimeField = <ResourceSelect label="Runtime connection" value={runtime?.id ?? ""}
    onChange={(id) => { setResume(null); setRuntimeId(id) }} addLabel="Connect runtime" onAdd={() => setConnecting(true)}>
    <option value="">{connections.isLoading ? "Loading connections…" : "Select connection"}</option>
    {runtimes.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
  </ResourceSelect>

  let form
  if (runtime && graph.data && isHostingPlatform(runtime.platform)) {
    form = <HostKeys key={runtime.id} connection={runtime} connections={accounts} runtimeField={runtimeField}
      credentials={graph.data.credentials} playbooks={playbooks.data ?? []} onFinish={onFinish}
      resume={resume?.source === "hosting" && resume.hostId === runtime.id ? resume.form : undefined} />
  } else if (runtime?.platform === "aws" && graph.data && environments.data) {
    form = <CredentialSetup key={runtime.id} isOpen runtime={runtime} runtimeField={runtimeField} graph={graph.data}
      connections={accounts} environments={environments.data} playbooks={playbooks.data ?? []}
      resume={resume?.source === "aws" ? resume.form : undefined} onClose={() => onFinish("")} onCreate={importAws} />
  } else {
    form = <SetupPage eyebrow="Inventory / Credentials" title="Add credential" steps={["Credential", "Controls", "Review"]} current={0}
      onBack={() => onFinish("")} onCancel={() => onFinish("")} primary={<Button disabled>Continue</Button>}>
      <div className="space-y-4">
        <FormGrid>{runtimeField}</FormGrid>
        {connections.isError && <RetryNotice onRetry={() => connections.refetch()}>Couldn’t load runtime connections.</RetryNotice>}
        {runtime && graph.isError && <RetryNotice onRetry={() => graph.refetch()}>Couldn’t load existing credentials.</RetryNotice>}
        {runtime?.platform === "aws" && environments.isError && <RetryNotice onRetry={() => environments.refetch()}>Couldn’t load environments.</RetryNotice>}
      </div>
    </SetupPage>
  }

  return <PageSection name="Inventory / Credentials">
    <div hidden={connecting}>{form}</div>
    {connecting && <ConnectionSetup initialRoles={["runtime"]} connections={accounts} playbooks={playbooks.data ?? []}
      onClose={closeConnection} onChanged={() => query.invalidateQueries({ queryKey: ["connections"] })}
      beforeAuthorization={(integration) => saveCredentialReturn(organisation, integration, { source: "hosting", hostId: "" })}
      onCreated={async (created) => { closeConnection(); setRuntimeId(created.id) }} />}
  </PageSection>
}
