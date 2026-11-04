import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { AwsPrepared } from "../lib/api/connections"
import type { Connection } from "../types"
import { Detail, DetailList } from "../components/detail"
import { IntegrationMark } from "../components/integration"
import { Failure, Loading } from "../components/state"
import { Button } from "../components/ui/button"
import { Field, FormGrid, SetupPage, formControl } from "../components/workspace"

function resources(value: string) {
  return value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean)
}

function download(setup: AwsPrepared) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(setup.template, null, 2)], { type: "application/json" }))
  const link = document.createElement("a")
  link.href = url
  link.download = "kinu-role.json"
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function AwsSetup({ onClose, onBack, onChanged, onCreated, connectionId, requiredRole }: {
  onClose: () => void
  onBack: () => void
  onChanged: () => Promise<void>
  onCreated?: (connection: Connection) => Promise<void>
  connectionId?: string
  requiredRole?: "runtime" | "secret-store"
}) {
  const [name, setName] = useState("AWS")
  const [account, setAccount] = useState("")
  const [region, setRegion] = useState("eu-west-2")
  const [secrets, setSecrets] = useState("")
  const [functions, setFunctions] = useState("")
  const [kms, setKms] = useState("")
  const saved = useQuery({ queryKey: ["aws-setup", connectionId], queryFn: () => api.getAwsSetup(connectionId!), enabled: Boolean(connectionId) })
  const prepare = useMutation({
    mutationFn: () => api.prepareAws({ account_id: account.trim(), display_name: name.trim(), region: region.trim(), secret_arns: resources(secrets), function_arns: resources(functions), kms_arns: resources(kms) }),
    onSuccess: onChanged,
  })
  const setup = prepare.data ?? saved.data
  const confirm = useMutation({
    mutationFn: () => api.confirmAws(setup!.connection),
    onSuccess: async (connection) => {
      await onChanged()
      if (onCreated) await onCreated(connection)
      else onClose()
    },
  })
  if (connectionId && saved.isLoading) return <div className="page"><Loading /></div>
  if (saved.error) return <div className="page"><Failure error={saved.error} /></div>
  const hasSecrets = resources(secrets).length > 0
  const hasFunctions = resources(functions).length > 0
  const valid = name.trim() && /^\d{12}$/.test(account.trim()) && region.trim() && (hasSecrets || hasFunctions)
    && (requiredRole !== "runtime" || hasFunctions) && (requiredRole !== "secret-store" || hasSecrets)

  return <SetupPage eyebrow="Inventory / Connections" title="Connect AWS" steps={["Resources", "AWS access"]} current={setup ? 1 : 0} onBack={onBack} onExit={onBack} onCancel={onClose} error={prepare.error?.message ?? confirm.error?.message} primary={setup ? <Button onClick={() => confirm.mutate()} disabled={confirm.isPending}>{confirm.isPending ? "Checking…" : "Connect"}</Button> : <Button onClick={() => prepare.mutate()} disabled={!valid || prepare.isPending}>{prepare.isPending ? "Preparing…" : "Continue"}</Button>}>
    {!setup ? <div className="space-y-7">
      <IntegrationMark kind="aws" name={name} onNameChange={setName} disabled={prepare.isPending} />
      <FormGrid>
      <Field label="AWS account ID"><input className={formControl} value={account} onChange={(event) => setAccount(event.target.value)} inputMode="numeric" placeholder="12-digit account ID" /></Field>
      <Field label="Region"><input className={formControl} value={region} onChange={(event) => setRegion(event.target.value)} /></Field>
      <Field label={requiredRole === "secret-store" ? "Secret ARNs (required)" : "Secret ARNs"}><textarea required={requiredRole === "secret-store"} className={`${formControl} h-24 py-3`} value={secrets} onChange={(event) => setSecrets(event.target.value)} placeholder="One complete Secrets Manager ARN per line" /></Field>
      <Field label={requiredRole === "runtime" ? "Lambda function ARNs (required)" : "Lambda function ARNs"}><textarea required={requiredRole === "runtime"} className={`${formControl} h-24 py-3`} value={functions} onChange={(event) => setFunctions(event.target.value)} placeholder="One unqualified function ARN per line" /></Field>
      <Field label="KMS key ARNs (optional)"><textarea className={`${formControl} h-24 py-3`} value={kms} onChange={(event) => setKms(event.target.value)} placeholder="Only for secrets encrypted with your own KMS keys" /></Field>
      </FormGrid>
    </div> : <div className="space-y-5">
      <ol className="list-decimal space-y-3 pl-5 text-[11px] leading-5 text-[var(--ink-soft)]">
        <li><Button variant="secondary" onClick={() => download(setup)}>Download access template</Button></li>
        <li>In account {account || setup.role_arn.split(":")[4]}, open <a className="underline" href={`https://${setup.connection.region}.console.aws.amazon.com/cloudformation/home?region=${setup.connection.region}#/stacks/create`} target="_blank" rel="noreferrer">CloudFormation</a> and upload the template.</li>
        <li>Review permissions, acknowledge the IAM role and create the stack. Then select Connect.</li>
      </ol>
      <DetailList><Detail label="Role"><span className="break-all">{setup.role_arn}</span></Detail><Detail label="Resources">{setup.connection.allowed_resources.length} selected</Detail></DetailList>
    </div>}
  </SetupPage>
}
