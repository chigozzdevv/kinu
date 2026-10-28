import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Plus, X } from "lucide-react"
import { api } from "../lib/api"
import type { GmailSender } from "../lib/api/gmail"
import type { Connection } from "../types"
import { Detail, DetailList } from "../components/detail"
import { IntegrationMark } from "../components/integration"
import { Failure, Loading, Notice } from "../components/state"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { ConnectPage, Field, SelectControl, SetupPage, formControl } from "../components/workspace"

export function GmailSetup({ connections, onBack, onClose, onChanged, onCreated, connectionId }: {
  connections: Connection[]
  onBack: () => void
  onClose: () => void
  onChanged: () => Promise<void>
  onCreated?: (connection: Connection) => Promise<void>
  connectionId?: string
}) {
  const [mailboxId, setMailboxId] = useState(() => connectionId ?? new URLSearchParams(window.location.search).get("gmail") ?? "")
  const validId = /^gmail_[a-f0-9]{32}$/.test(mailboxId)
  const [disconnecting, setDisconnecting] = useState(false)
  const [senders, setSenders] = useState<GmailSender[] | null>(null)
  const mailbox = useQuery({ queryKey: ["gmail", mailboxId], queryFn: () => api.getGmail(mailboxId), enabled: validId, retry: false })
  const accounts = useQuery({ queryKey: ["gmail-accounts"], queryFn: () => api.listGmail(), enabled: !validId, retry: false })
  const providers = connections.filter((item) => item.status === "ready" && item.roles.includes("provider") && item.interface === "api")
  const rows = senders ?? (mailbox.data?.senders.length ? mailbox.data.senders : [{ sender: "", provider_connection_id: providers[0]?.id ?? "" }])
  const begin = useMutation({ mutationFn: () => api.beginGmail(validId ? mailboxId : undefined), onSuccess: (value) => { window.location.assign(value.authorization_url) } })
  const save = useMutation({ mutationFn: () => api.saveGmail(mailbox.data!, rows.map((item) => ({ ...item, sender: item.sender.trim() }))), onSuccess: async (box) => {
    await onChanged()
    const connection = await api.getConnection(box.id)
    if (onCreated) await onCreated(connection)
    else onClose()
  } })
  const disconnect = useMutation({ mutationFn: () => api.disconnectGmail(mailbox.data!), onSuccess: async () => { await onChanged(); onClose() } })
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("gmail")) window.history.replaceState({}, "", window.location.pathname)
  }, [])
  const callbackError = mailboxId === "expired" ? "Connection expired. Connect Gmail again." : mailboxId === "failed" ? "Gmail was not connected. Try again." : ""
  if (validId && mailbox.isLoading) return <div className="page"><Loading /></div>
  if (mailbox.error) return <div className="page"><Failure error={mailbox.error} /></div>
  if (!mailbox.data) return <ConnectPage eyebrow="Inventory / Connections" title="Gmail" onBack={onBack} onClose={onClose} error={begin.error?.message || accounts.error?.message || callbackError} action={<Button onClick={() => begin.mutate()} disabled={begin.isPending}>{begin.isPending ? "Connecting…" : "Connect Gmail"}</Button>}><IntegrationMark kind="gmail" /><p className="mt-5 text-center text-[11px] text-[var(--ink-muted)]">Read alerts from the senders you choose.</p>{accounts.data?.filter((item) => item.status !== "disabled").map((item) => <Button key={item.id} variant="secondary" className="mt-3 w-full" onClick={() => { setSenders(null); setMailboxId(item.id) }}>{item.email}</Button>)}</ConnectPage>
  function update(index: number, value: Partial<GmailSender>) { setSenders(rows.map((item, position) => position === index ? { ...item, ...value } : item)) }
  const ready = mailbox.data.status !== "reauthentication-required" && mailbox.data.status !== "disabled"
  const valid = ready && rows.length > 0 && rows.every((item) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(item.sender.trim()) && providers.some((provider) => provider.id === item.provider_connection_id))
  return <>
    <SetupPage eyebrow="Inventory / Connections" title="Gmail" steps={["Account", "Alert senders"]} current={1} onBack={onBack} onExit={onBack} onCancel={onClose} error={save.error?.message || begin.error?.message || disconnect.error?.message} primary={ready ? <Button onClick={() => save.mutate()} disabled={!valid || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button> : <Button onClick={() => begin.mutate()} disabled={begin.isPending}>{begin.isPending ? "Connecting…" : "Reconnect Gmail"}</Button>}>
      <div className="space-y-5">
        <DetailList><Detail label="Account">{mailbox.data.email}</Detail></DetailList>
        {!ready && <Notice>Reconnect Gmail to resume monitoring.</Notice>}
        {!providers.length && <Notice>Connect a credential provider first.</Notice>}
        {rows.map((item, index) => <div key={index} className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <Field label="Sender"><input type="email" className={formControl} value={item.sender} placeholder="security@provider.com" onChange={(event) => update(index, { sender: event.target.value })} /></Field>
          <Field label="Provider"><SelectControl value={item.provider_connection_id} onChange={(event) => update(index, { provider_connection_id: event.target.value })}><option value="">Select provider</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.display_name}</option>)}</SelectControl></Field>
          <Button variant="ghost" disabled={rows.length === 1} aria-label={`Remove sender ${index + 1}`} onClick={() => setSenders(rows.filter((_, position) => position !== index))}><X className="size-4" /></Button>
        </div>)}
        <div className="flex justify-between"><Button variant="ghost" disabled={rows.length >= 20} onClick={() => setSenders([...rows, { sender: "", provider_connection_id: providers[0]?.id ?? "" }])}><Plus className="size-3.5" />Add sender</Button><Button variant="ghost" onClick={() => setDisconnecting(true)}>Disconnect</Button></div>
      </div>
    </SetupPage>
    <Modal isOpen={disconnecting} onClose={() => setDisconnecting(false)} title="Disconnect Gmail?" actions={<Button onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>{disconnect.isPending ? "Disconnecting…" : "Disconnect"}</Button>}>
      <p className="text-[11px] leading-5 text-[var(--ink-muted)]">Stops monitoring and removes saved access for this workspace.</p>
      <a className="mt-3 inline-block text-[11px] underline" href="https://myaccount.google.com/connections" target="_blank" rel="noreferrer">Manage Google permissions</a>
    </Modal>
  </>
}
