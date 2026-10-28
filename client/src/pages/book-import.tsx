import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Upload } from "lucide-react"
import { api, type PlaybookDefinition, type PlaybookDetail } from "../lib/api"
import type { Playbook } from "../types"
import { Detail, DetailList, Section } from "../components/detail"
import { Field, SetupPage } from "../components/workspace"
import { Button } from "../components/ui/button"

export function BookImport({ onClose, onCreated, existing }: { onClose: () => void; onCreated: (book: Playbook) => Promise<void>; existing?: PlaybookDetail }) {
  const [definition, setDefinition] = useState<PlaybookDefinition | null>(null)
  const [filename, setFilename] = useState("")
  const [error, setError] = useState("")
  const publish = useMutation({
    mutationFn: async () => {
      const id = existing?.playbook.id ?? `playbook_${crypto.randomUUID().replaceAll("-", "")}`
      const result = await api.importPlaybook(id, definition!)
      await api.publishPlaybook(result.playbook.id, result.version.id)
      return (await api.getPlaybook(result.playbook.id)).playbook
    },
    onSuccess: onCreated,
  })
  async function load(file?: File) {
    if (!file) return
    setError("")
    setDefinition(null)
    publish.reset()
    try {
      if (file.size > 100000) throw new Error("Use a definition under 100 KB.")
      const value: PlaybookDefinition = JSON.parse(await file.text())
      if (!value || typeof value.name !== "string" || !value.name || typeof value.platform !== "string" || !value.platform || !Array.isArray(value.allowed_domains) || !value.allowed_domains.length || value.allowed_domains.some((domain) => typeof domain !== "string") || !Array.isArray(value.steps) || !value.steps.length || value.steps.some((step) => !step || typeof step.id !== "string" || typeof step.objective !== "string") || !value.verifier) throw new Error("Include provider domains, a browser procedure and a credential verifier.")
      setDefinition(value)
      setFilename(file.name)
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Invalid definition.") }
  }
  return <SetupPage eyebrow="Inventory / Playbooks" title={existing ? "Import version" : "Import playbook"} steps={["Definition", "Review"]} current={definition ? 1 : 0} onBack={() => setDefinition(null)} onExit={onClose} onCancel={onClose} error={error || publish.error?.message}
    primary={<Button onClick={() => publish.mutate()} disabled={!definition || publish.isPending}>{publish.isPending ? "Publishing…" : "Publish playbook"}</Button>}>
    {!definition ? <Field label="Browser definition"><label className="focus-ring flex h-11 cursor-pointer items-center rounded-xl border border-[var(--border)] bg-white px-4 text-[11px]"><Upload className="mr-2 size-3.5" />{filename || "Choose JSON definition"}<input className="sr-only" type="file" accept="application/json,.json" onChange={(event) => void load(event.target.files?.[0])} /></label></Field> : <div className="grid gap-6 lg:grid-cols-2"><Section title="Definition" onEdit={() => setDefinition(null)}><DetailList><Detail label="Name">{definition.name}</Detail><Detail label="Provider">{definition.platform}</Detail><Detail label="Domains">{definition.allowed_domains.join(", ")}</Detail></DetailList></Section><Section title="Procedure"><div className="space-y-3">{definition.steps.map((step, index) => <div key={step.id} className="text-[12px]">{index + 1}. {step.objective}</div>)}</div></Section></div>}
  </SetupPage>
}
