import type { Incident, InventoryGraph } from "../types"
import { titleCase } from "./format.ts"

export function affectedResource(incident: Incident, graph?: InventoryGraph): { name: string; detail: string | null } {
  const resource = incident.resource
  const reported = resource.service || resource.repository || resource.project
  if (reported) return { name: reported, detail: [resource.project, resource.environment].filter(value => value && value !== reported).join(" · ") || null }
  const credentialId = incident.credential_id
  const credential = graph?.credentials.find(item => item.id === credentialId && item.organisation_id === incident.organisation_id)
  if (!credential) return { name: "Not identified", detail: null }
  const serviceIds = new Set(graph?.bindings.filter(item => item.credential_id === credential.id && item.organisation_id === incident.organisation_id).map(item => item.service_id))
  const services = graph?.services.filter(item => serviceIds.has(item.id) && item.organisation_id === incident.organisation_id) ?? []
  if (services.length) {
    const locations = services.flatMap(item => {
      try {
        const location = new URL(item.runtime_resource)
        const platform = titleCase(location.protocol.slice(0, -1))
        const environment = location.pathname.split("/").filter(Boolean).at(-1)
        return [platform + (environment && ["production", "development", "preview", "staging", "test"].includes(environment) ? " · " + titleCase(environment) : "")]
      } catch { return [] }
    })
    return { name: services.map(item => item.display_name).join(", "), detail: [...new Set(locations)].join(", ") || null }
  }
  return { name: credential.display_name, detail: credential.provider }
}
