import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Detail, DetailList, Section } from "./detail"
import { Provider } from "./provider"
import { Button } from "./ui/button"
import { Field, FormGrid, SetupPage, formControl } from "./workspace"
import { api, type PlaybookDetail, type PreparedPlaybook } from "../lib/api"
import { useOrganisation } from "../lib/useorganisation"
import type { ApiPlaybookDetail, PreparedApiPlaybook } from "../lib/api/api-playbooks"
import { ApiOperations } from "./api-operations"
import type { Playbook } from "../types"
import { apiSourceError } from "../lib/playbook-source"

const steps = ["Source", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

export function PlaybookSetup({ onClose, onCreated, existing, initialPlatform = "", execution = "browser", showIntroduction = true }: { onClose: () => void; onCreated: (playbook: Playbook) => Promise<void>; existing?: PlaybookDetail | ApiPlaybookDetail; initialPlatform?: string; execution?: "browser" | "api"; showIntroduction?: boolean }) {
  const { active } = useOrganisation()
  const canPublish = active.role === "administrator"
  const [step, setStep] = useState(0)
  const [sourceMode, setSourceMode] = useState<"instructions" | "links">("instructions")
  const [source, setSource] = useState("")
  const [sourceTouched, setSourceTouched] = useState(false)
  const [links, setLinks] = useState("")
  const urls = [...new Set(links.split(/\r?\n/).map((url) => url.trim()).filter(Boolean))]
  const [prepared, setPrepared] = useState<PreparedPlaybook | PreparedApiPlaybook | null>(null)
  const prepare = useMutation({ mutationFn: async (): Promise<PreparedPlaybook | PreparedApiPlaybook> => (execution === "api" ? api.prepareApiPlaybook : api.preparePlaybook)({
    playbook_id: existing?.playbook.id ?? identifier("playbook"),
    version_id: identifier("playbook_version"),
    sources: sourceMode === "instructions"
      ? [{ id: identifier("source_text"), kind: "text" as const, text: source.trim() }]
      : urls.map((url) => ({ id: identifier("source_link"), kind: "link" as const, resource: url })),
    objective: existing
      ? `Build the next ${execution} credential-rotation procedure. Use the exact name ${JSON.stringify(existing.playbook.name)} and exact platform ${JSON.stringify(existing.playbook.platform)}.`
      : initialPlatform
        ? `Build a ${execution} credential-rotation procedure for the exact platform ${JSON.stringify(slug(initialPlatform))}.`
        : `Build a ${execution} credential-rotation procedure from the supplied source.`,
  }), onSuccess: (result) => { setPrepared(result); setStep(1) } })
  const save = useMutation({ mutationFn: async () => {
    if (!prepared) throw new Error("Generate the playbook first")
    const { definition, ...identity } = prepared
    const isApi = "http" in definition
    const book = "http" in definition
      ? await api.saveApiPlaybook({ ...identity, definition })
      : await api.savePlaybook({ ...identity, definition })
    if (!canPublish) return book
    if (isApi) {
      await api.publishApiPlaybook(book.id, prepared.version_id)
      return (await api.getApiPlaybook(book.id)).playbook
    }
    await api.publishPlaybook(book.id, prepared.version_id)
    return (await api.getPlaybook(book.id)).playbook
  }, onSuccess: onCreated })
  const sourceError = execution === "api" && sourceMode === "instructions" ? apiSourceError(source) : undefined
  const visibleSourceError = sourceTouched ? sourceError : undefined
  const sourceReady = sourceMode === "instructions" ? Boolean(source.trim()) && !sourceError : urls.length > 0 && urls.length <= 5

  return <SetupPage eyebrow="Inventory / Playbooks" title={existing ? "New playbook version" : execution === "api" ? "Add API playbook" : "Add browser playbook"} steps={steps} current={step} onBack={() => { setStep(0); save.reset() }} onCancel={onClose} error={(prepare.error ?? save.error)?.message} primary={step === 0 ? <Button onClick={() => { save.reset(); prepare.mutate() }} disabled={!sourceReady || prepare.isPending}>{prepare.isPending ? "Generating…" : "Generate"}</Button> : <Button onClick={() => save.mutate()} disabled={!prepared || save.isPending}>{save.isPending ? "Saving…" : canPublish ? "Publish playbook" : "Save draft"}</Button>}>
    {step === 0 && <div className="space-y-5">
      {showIntroduction && <p className="text-[11px] leading-5 text-[var(--ink-soft)]">A playbook teaches the agent how to use {execution === "api" ? "the API" : "the provider’s website"}. Add a docs link or instructions.</p>}
      <FormGrid>
        <div role="group" aria-label="Source type" className="flex gap-2 sm:col-span-2">{(["instructions", "links"] as const).map((mode) => <Button key={mode} size="sm" variant={sourceMode === mode ? "primary" : "secondary"} aria-pressed={sourceMode === mode} disabled={prepare.isPending} onClick={() => { setSourceMode(mode); setPrepared(null); prepare.reset(); save.reset() }}>{mode === "instructions" ? "Instructions" : "Links"}</Button>)}</div>
        {sourceMode === "instructions"
          ? <div className="sm:col-span-2"><Field label="Instructions"><textarea className={`${formControl} h-40 py-3`} value={source} disabled={prepare.isPending} maxLength={100000} onBlur={() => setSourceTouched(true)} aria-invalid={Boolean(visibleSourceError)} aria-describedby={visibleSourceError ? "playbook-source-error" : undefined} onChange={(event) => { setSource(event.target.value); prepare.reset() }} placeholder={execution === "api" ? "Describe the list, create, verify and revoke API operations." : "Describe how to create and revoke the credential."} /></Field>{visibleSourceError && <p id="playbook-source-error" role="alert" className="mt-2 text-[10px] text-[var(--red)]">{visibleSourceError}</p>}</div>
          : <Field label="Links" hint="One documentation link per line, up to five." wide><textarea className={`${formControl} h-40 py-3`} value={links} disabled={prepare.isPending} onChange={(event) => { setLinks(event.target.value); prepare.reset() }} placeholder="https://docs.example.com/api-keys" /></Field>}
      </FormGrid>
    </div>}
    {step === 1 && prepared && <div className="grid gap-5 lg:grid-cols-2"><div><Section title="Source" onEdit={() => setStep(0)}><DetailList>{sourceMode === "instructions" ? <Detail label="Instructions">{source.trim().slice(0, 240)}</Detail> : <Detail label="Links">{urls.map((url) => <div key={url}>{url}</div>)}</Detail>}</DetailList></Section><Section title="Playbook"><DetailList><Detail label="Name">{prepared.definition.name}</Detail><Detail label="Platform"><Provider value={prepared.definition.platform} /></Detail>{"http" in prepared.definition ? <Detail label="API">{prepared.definition.http.base_url}</Detail> : <Detail label="Domains">{prepared.definition.allowed_domains.join(", ")}</Detail>}</DetailList></Section></div><Section title="Procedure"><div className="space-y-1">{"steps" in prepared.definition ? prepared.definition.steps.map((action, index) => <div key={action.id} className="grid grid-cols-[24px_1fr] gap-2 py-2 text-[10px]"><span className="text-[var(--ink-muted)]">{index + 1}</span><span>{action.objective}</span></div>) : <ApiOperations definition={prepared.definition.http} />}</div></Section></div>}
    {step === 1 && prepared && <details className="mt-6 text-[11px]"><summary className="focus-ring w-fit cursor-pointer rounded-md font-semibold">View definition</summary><pre className="mt-3 max-h-96 overflow-auto rounded-xl border border-[var(--border)] bg-white p-4 text-[10px]">{JSON.stringify(prepared.definition, null, 2)}</pre></details>}
  </SetupPage>
}
