import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Failure, Loading } from "../components/state"
import { Field, formControl } from "../components/workspace"
import { api } from "../lib/api"
import type { Integration } from "../lib/api/integrations"

const labels: Record<string, string> = { client_id: "Client ID", client_secret: "Client secret", slug: "Integration slug" }

export function IntegrationsSettings() {
  const settings = useQuery({ queryKey: ["integrations"], queryFn: () => api.getIntegrations() })
  const [selected, setSelected] = useState<Integration | null>(null)
  if (settings.isLoading) return <Loading />
  if (settings.error) return <Failure error={settings.error} />
  return <div className="max-w-[640px]">
    <div className="divide-y divide-[var(--border-soft)]">
      {settings.data?.integrations.map((item) => <div key={item.id} className="flex min-h-[72px] items-center justify-between gap-4 py-4">
        <div className="flex min-w-0 flex-col items-start gap-2 sm:flex-row sm:items-center sm:gap-8">
          <span className="text-[11px] font-medium sm:w-32 sm:shrink-0">{item.name}</span>
          <Badge className="whitespace-nowrap" variant={item.configured ? "healthy" : "neutral"}>{item.fields.length === 0 ? "No app setup" : item.configured ? "Configured" : "Setup required"}</Badge>
        </div>
        {settings.data.editable && item.fields.length > 0 && <Button className="shrink-0" variant="secondary" onClick={() => setSelected(item)}>Configure</Button>}
      </div>)}
    </div>
    {selected && <Configure key={selected.id} integration={selected} onClose={() => setSelected(null)} />}
  </div>
}

function Configure({ integration, onClose }: { integration: Integration, onClose: () => void }) {
  const query = useQueryClient()
  const [values, setValues] = useState<Record<string, string>>({})
  const save = useMutation({
    mutationFn: () => api.configureIntegration(integration.id, values),
    onSuccess: async () => {
      await Promise.all([query.invalidateQueries({ queryKey: ["integrations"] }), query.invalidateQueries({ queryKey: ["features"] })])
      onClose()
    },
  })
  return <Modal isOpen onClose={onClose} title={`Configure ${integration.name}`} actions={<Button onClick={() => save.mutate()} disabled={save.isPending || !Object.values(values).some(Boolean)}>{save.isPending ? "Saving…" : "Save"}</Button>}>
    <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); save.mutate() }}>
      {integration.callback_url && <Field label="Callback URL"><input className={formControl} value={integration.callback_url} readOnly onFocus={(event) => event.target.select()} /></Field>}
      {integration.fields.map((field) => <Field key={field} label={labels[field] ?? field}>
        <input className={formControl} type={field.includes("secret") ? "password" : "text"} autoComplete="off"
          value={values[field] ?? ""} placeholder={integration.configured ? "Keep existing" : ""}
          onChange={(event) => setValues({ ...values, [field]: event.target.value })} />
      </Field>)}
      {save.error && <Failure error={save.error} />}
    </form>
  </Modal>
}
