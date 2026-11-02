import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { ChevronRight } from "lucide-react"
import { PageHeader } from "../components/header"
import { CredentialSetup } from "../components/setup"
import { Button } from "../components/ui/button"
import { activeOrganisationId, api, type ImportCredentialInput } from "../lib/api"
import { connectionCallbackIntegration } from "../lib/callback"
import { credentialReturn, finishCredentialConnection, saveCredentialReturn } from "../lib/credential-return"
import type { Connection, Environment, InventoryGraph, Playbook } from "../types"
import { ConnectionSetup } from "./connections"
import { HostKeys } from "./host-keys"

export function CredentialOnboarding({ graph, connections, environments, playbooks, onFinish }: {
  graph: InventoryGraph
  connections: Connection[]
  environments: Environment[]
  playbooks: Playbook[]
  onFinish: (credentialId: string) => void
}) {
  const query = useQueryClient()
  const organisation = activeOrganisationId()
  const [resume, setResume] = useState(() => credentialReturn(organisation, connectionCallbackIntegration()))
  const [source, setSource] = useState(resume?.source === "aws" ? "aws" : resume?.hostId ?? "")
  const [connecting, setConnecting] = useState(resume?.source === "hosting" && !resume.hostId)
  const hosts = connections.filter((item) => ["vercel", "railway", "render"].includes(item.platform) && item.status === "ready")
  const host = hosts.find((item) => item.id === source)

  function closeConnection() {
    finishCredentialConnection()
    setResume(null)
    setConnecting(false)
  }

  async function importAws(input: ImportCredentialInput) {
    const result = await api.importCredential(input)
    await Promise.all([query.invalidateQueries({ queryKey: ["graph"] }), query.invalidateQueries({ queryKey: ["overview"] })])
    onFinish(result.id)
  }

  if (connecting) return <ConnectionSetup initialRoles={["runtime"]} integrations={["vercel", "railway", "render"]}
    connections={connections} playbooks={playbooks} onClose={closeConnection}
    beforeAuthorization={(integration) => saveCredentialReturn(organisation, integration, { source: "hosting", hostId: "" })}
    onChanged={() => query.invalidateQueries({ queryKey: ["connections"] })}
    onCreated={async (created) => { closeConnection(); setSource(created.id) }} />

  if (source === "aws") return <CredentialSetup isOpen graph={graph} connections={connections} environments={environments}
    playbooks={playbooks} resume={resume?.source === "aws" ? resume.form : undefined}
    onClose={() => onFinish("")} onCreate={importAws} />

  if (host) return <HostKeys key={host.id} connection={host} connections={connections} credentials={graph.credentials} playbooks={playbooks}
    resume={resume?.source === "hosting" && resume.hostId === host.id ? resume.form : undefined}
    onBack={() => { setResume(null); setSource("") }} onFinish={onFinish} />

  return <div className="page"><PageHeader eyebrow="Inventory / Credentials" title="Add credential" onBack={() => onFinish("")} />
    <div className="max-w-[620px] space-y-3">
      <p className="mb-5 text-[12px] text-[var(--ink-muted)]">Choose where your application uses its keys.</p>
      {[...hosts.map((item) => ({ id: item.id, name: item.display_name })), { id: "aws", name: "AWS Secrets Manager" }].map((item) => (
        <button key={item.id} className="focus-ring content-hover flex w-full items-center justify-between rounded-xl border border-[var(--border-soft)] p-4 text-left text-[12px]" onClick={() => setSource(item.id)}>
          {item.name}<ChevronRight className="content-chevron size-4" />
        </button>
      ))}
      <Button variant="secondary" onClick={() => setConnecting(true)}>Connect hosting</Button>
    </div>
  </div>
}
