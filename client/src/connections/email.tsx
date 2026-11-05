import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Copy } from "lucide-react"
import { IntegrationMark } from "../components/integration"
import { Button } from "../components/ui/button"
import { ConnectPage, SetupPage } from "../components/workspace"
import { Failure, Loading } from "../components/state"
import { api } from "../lib/api"
import type { ProviderSetupProps } from "./provider-setup-props"

export function EmailSetup({ onBack, onClose, onChanged, onCreated }: ProviderSetupProps) {
  const cache = useQueryClient()
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState("")
  const inbox = useQuery({ queryKey: ["email-inbox"], queryFn: api.getEmailInbox, refetchInterval: 5_000 })
  const create = useMutation({ mutationFn: api.createEmailInbox, onSuccess: async (value) => {
    cache.setQueryData(["email-inbox"], value)
    await onChanged()
  } })
  const finish = useMutation({ mutationFn: async () => {
    await onChanged()
    if (onCreated && inbox.data) await onCreated(await api.getConnection(inbox.data.id))
    else onClose()
  } })
  if (inbox.isLoading) return <div className="page"><Loading /></div>
  if (inbox.error) return <div className="page"><Failure error={inbox.error} /><Button className="mt-4" variant="secondary" onClick={onBack}>Back</Button></div>
  if (!inbox.data) return <ConnectPage eyebrow="Inventory / Connections" title="Email alerts" onBack={onBack} onClose={onClose} error={create.error?.message} action={<Button disabled={create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Connecting…" : "Connect"}</Button>}>
    <IntegrationMark kind="email" />
    <p className="max-w-64 text-center text-[11px] leading-5 text-[var(--ink-soft)]">Forward alerts to your workspace inbox.</p>
  </ConnectPage>
  const box = inbox.data
  return <SetupPage eyebrow="Inventory / Connections" title="Email alerts" steps={["Forward alerts"]} current={0} onBack={onBack} onExit={onBack} onCancel={onClose} error={finish.error?.message || copyError} primary={<Button disabled={finish.isPending} onClick={() => finish.mutate()}>{finish.isPending ? "Saving…" : "Done"}</Button>}>
    <div className="space-y-6">
      <div>
        <p className="mb-3 text-[11px] font-medium text-[var(--ink-soft)]">Forwarding address</p>
        <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] px-4 py-3">
          <code className="min-w-0 flex-1 break-all text-[12px] select-all">{box.address}</code>
          <Button variant="ghost" size="sm" aria-label={copied ? "Address copied" : "Copy email address"} onClick={async () => {
            try { await navigator.clipboard.writeText(box.address); setCopied(true); setCopyError("") }
            catch { setCopyError("Select the address and copy it manually.") }
          }}>{copied ? <Check className="size-4" /> : <Copy className="size-4" />}</Button>
        </div>
      </div>
      <p className="text-[11px] leading-5 text-[var(--ink-soft)]">Add this address in your mailbox’s forwarding settings, then enable forwarding.</p>
      {box.confirmation_url && <a className="focus-ring inline-flex rounded-lg bg-[var(--ink)] px-4 py-2 text-[11px] font-semibold text-white" href={box.confirmation_url} target="_blank" rel="noreferrer">Confirm forwarding ↗</a>}
      {(!box.confirmation_url || box.receiving) && <p className="text-[11px] leading-5 text-[var(--ink-soft)]" role="status">{box.receiving ? "Receiving email" : "Waiting for email"}</p>}
    </div>
  </SetupPage>
}
