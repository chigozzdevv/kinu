import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Detail, DetailList } from "../components/detail"
import { IntegrationMark } from "../components/integration"
import { Notice } from "../components/state"
import { Button } from "../components/ui/button"
import { ConnectPage, Field, SelectControl, SetupPage, formControl } from "../components/workspace"
import { api } from "../lib/api"
import type { Connection } from "../types"

export function ResendSetup({ onClose, onBack, onChanged, onCreated }: {
  onClose: () => void; onBack: () => void; onChanged: () => Promise<void>;
  onCreated?: (connection: Connection) => Promise<void>;
}) {
  const [step, setStep] = useState(0)
  const [token, setToken] = useState("")
  const [domain, setDomain] = useState("")
  const domains = useMutation({ mutationFn: () => api.getResendDomains(token.trim()), onSuccess: (items) => {
    setDomain(items[0]?.id ?? "")
    setStep(1)
  } })
  const connect = useMutation({
    mutationFn: () => api.connectProvider({ display_name: "Resend", platform: "resend", token: token.trim(), domain_id: domain }),
    onSuccess: async (connection) => {
      setToken("")
      await onChanged()
      await onCreated?.(connection)
      onClose()
    },
  })
  if (step === 0) return <ConnectPage eyebrow="Inventory / Connections" title="Connect Resend" onBack={onBack} onClose={onClose}
    error={domains.error?.message} action={<Button onClick={() => domains.mutate()} disabled={!token.trim() || domains.isPending}>{domains.isPending ? "Checking…" : "Continue"}</Button>}>
    <IntegrationMark kind="resend" />
    <div className="mt-6 w-full"><Field label="Management token" hint="A full-access token manages keys; workload keys stay sending-only."><input className={formControl} type="password" autoComplete="off" value={token} onChange={(event) => { setToken(event.target.value); domains.reset() }} /></Field></div>
  </ConnectPage>
  const selected = domains.data?.find((item) => item.id === domain)
  return <SetupPage eyebrow="Inventory / Connections" title="Connect Resend" steps={["Access", "Domain", "Review"]} current={step}
    onBack={() => setStep(step - 1)} onExit={onBack} onCancel={onClose} error={connect.error?.message}
    primary={step === 1 ? <Button onClick={() => setStep(2)} disabled={!selected}>Continue</Button> : <Button onClick={() => connect.mutate()} disabled={!selected || connect.isPending}>{connect.isPending ? "Connecting…" : "Connect"}</Button>}>
    {step === 1 ? <div className="max-w-[320px]">{!domains.data?.length ? <Notice>Verify a sending domain in Resend first.</Notice> : <Field label="Sending domain"><SelectControl value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.data.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</SelectControl></Field>}</div> : <div className="max-w-[480px]"><DetailList><Detail label="Provider">Resend</Detail><Detail label="Domain">{selected?.name}</Detail><Detail label="Workload permission">Sending only</Detail></DetailList></div>}
  </SetupPage>
}
