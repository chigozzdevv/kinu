import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Detail, DetailList } from "../components/detail"
import { Button } from "../components/ui/button"
import { Field, ResourceSelect, SetupPage, formControl } from "../components/workspace"
import { PlaybookSetup } from "../components/playbook-setup"
import { api } from "../lib/api"
import type { ProviderSetupProps } from "./provider-setup-props"

export function CustomApiSetup({ onClose, onBack, onChanged, onCreated }: ProviderSetupProps) {
  const query = useQueryClient()
  const [step, setStep] = useState(0)
  const [bookId, setBookId] = useState("")
  const [adding, setAdding] = useState(false)
  const [token, setToken] = useState("")
  const books = useQuery({ queryKey: ["api-playbooks"], queryFn: api.getApiPlaybooks })
  const detail = useQuery({ queryKey: ["api-playbooks", bookId], queryFn: () => api.getApiPlaybook(bookId), enabled: Boolean(bookId) })
  const book = detail.data?.playbook
  const version = detail.data?.active_version
  const create = useMutation({ mutationFn: () => api.connectProvider({
    display_name: book!.name, platform: book!.platform,
    api_playbook_id: book!.id, api_playbook_version_id: version!.id, token,
  }), onSuccess: async (connection) => {
    setToken("")
    await onChanged()
    if (onCreated) await onCreated(connection)
    else onClose()
  } })

  if (adding) return <PlaybookSetup execution="api" showIntroduction={false} onClose={() => setAdding(false)} onCreated={async (created) => {
    await query.invalidateQueries({ queryKey: ["api-playbooks"] })
    setBookId(created.active_version_id ? created.id : "")
    setAdding(false)
  }} />

  return <SetupPage eyebrow="Inventory / Connections" title="Custom API" steps={["Playbook", "Access", "Review"]}
    current={step} onBack={() => setStep((value) => value - 1)} onExit={onBack} onCancel={onClose}
    error={(books.error ?? detail.error ?? create.error)?.message}
    primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={step === 0 ? !version || detail.isFetching : !token}>Continue</Button>
      : <Button onClick={() => create.mutate()} disabled={create.isPending || !version || detail.isFetching}>{create.isPending ? "Connecting…" : "Connect"}</Button>}>
    {step === 0 && <div className="space-y-4">
      <ResourceSelect label="Playbook" hideLabel value={bookId} onChange={(id) => { setBookId(id); setToken(""); create.reset() }} addLabel="Add playbook" onAdd={() => setAdding(true)}>
        <option value="">{books.isLoading ? "Loading…" : "Select playbook"}</option>
        {(books.data ?? []).filter((item) => item.active_version_id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </ResourceSelect>
    </div>}
    {step === 1 && <Field label="Management token" hint="Stored in Kinu vault. Used only by the executor."><input className={formControl} type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></Field>}
    {step === 2 && <DetailList><Detail label="Playbook">{book?.name}</Detail><Detail label="API">{version?.definition.http.base_url}</Detail></DetailList>}
  </SetupPage>
}
