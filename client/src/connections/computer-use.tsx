import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Detail, DetailList, Section } from "../components/detail"
import { Provider } from "../components/provider"
import { Notice } from "../components/state"
import { Button } from "../components/ui/button"
import { ResourceSelect, SetupPage, SuccessPage } from "../components/workspace"
import type { Connection, Playbook } from "../types"
import { activeOrganisationId, api, type BrowserSetupResponse, type CreateConnectionInput } from "../lib/api"
import { PlaybookSetup } from "../components/playbook-setup"
import { BrowserSetupModal } from "./browser-session"
import { openBrowserSetup } from "./management"
import type { ProviderSetupProps } from "./provider-setup-props"

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

export function ComputerUseSetup({ onClose, onBack, onChanged, onCreated, playbooks }: ProviderSetupProps & { playbooks: Playbook[] }) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState(0)
  const [playbookVersion, setPlaybookVersion] = useState(playbooks.find((item) => item.active_version_id)?.active_version_id ?? "")
  const [created, setCreated] = useState<Connection | null>(null)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const [draftNotice, setDraftNotice] = useState(false)
  const [browserSetup, setBrowserSetup] = useState<BrowserSetupResponse | null>(null)
  const chosenPlaybook = playbooks.find((item) => item.active_version_id === playbookVersion)
  useEffect(() => {
    if (playbookVersion) return
    setPlaybookVersion(playbooks.find((item) => item.active_version_id)?.active_version_id ?? "")
  }, [playbookVersion, playbooks])
  const playbookDetail = useQuery({ queryKey: ["playbooks", chosenPlaybook?.id], queryFn: () => api.getPlaybook(chosenPlaybook!.id), enabled: Boolean(chosenPlaybook) })
  const create = useMutation({ mutationFn: (input: CreateConnectionInput) => api.createConnection(input) })
  const open = useMutation({ mutationFn: openBrowserSetup })
  const definition = playbookDetail.data?.active_version?.definition
  async function submit() {
    if (!chosenPlaybook || !definition) return
    const timestamp = new Date().toISOString()
    const result = await create.mutateAsync({ connection: {
      id: identifier("conn"), organisation_id: activeOrganisationId(), platform: chosenPlaybook.platform, display_name: chosenPlaybook.name, roles: ["provider"], interface: "browser", authorization: "browser-session", authorization_reference: null,
      capabilities: ["browser.authenticate", "browser.execute", "browser.secureCapture", "provider.listCredentialMetadata", "provider.createCredential", "provider.revokeCredential", "provider.testCredential"], allowed_resources: definition.allowed_domains, http: null, playbook_id: null, playbook_version_id: null, status: "setup-required", authenticated_at: null, authorization_expires_at: null, last_validated_at: null,
      verification_connection_id: null,
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

  if (creatingPlaybook) return <PlaybookSetup execution="browser" showIntroduction={false} onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => { await queryClient.invalidateQueries({ queryKey: ["playbooks"] }); setPlaybookVersion(playbook.active_version_id ?? ""); setDraftNotice(!playbook.active_version_id); setCreatingPlaybook(false) }} />
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
    {step === 0 && <div className="space-y-5"><p className="text-[10px] leading-5 text-[var(--ink-muted)]">Rotate keys through the provider dashboard.</p>{draftNotice && <Notice>Saved as draft — an administrator can publish it from Playbooks.</Notice>}<ResourceSelect label="Playbook" size="sm" width="compact" value={playbookVersion} onChange={setPlaybookVersion} addLabel="Add playbook" onAdd={() => { setDraftNotice(false); setCreatingPlaybook(true) }}><option value="">Select playbook</option>{playbooks.map((item) => item.active_version_id ? <option key={item.id} value={item.active_version_id}>{item.name}</option> : <option key={item.id} value="" disabled>{item.name} (draft)</option>)}</ResourceSelect></div>}
    {step === 1 && <Section title="Computer Use" onEdit={() => setStep(0)}><DetailList><Detail label="Playbook">{chosenPlaybook?.name}</Detail><Detail label="Platform"><Provider value={chosenPlaybook?.platform ?? ""} /></Detail><Detail label="Access">Secure browser</Detail></DetailList></Section>}
  </SetupPage>
}
