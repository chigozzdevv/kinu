import type { Incident, InventoryGraph, ManagedCredential } from "../types"
import { titleCase } from "./format.ts"

export function currentIncident(incidents: readonly Incident[], credential: ManagedCredential) {
  const open = incidents.filter((incident) => incident.organisation_id === credential.organisation_id
    && incident.credential_id === credential.id && !["resolved", "dismissed"].includes(incident.status))
  return open.find((incident) => incident.status === "action-required") ?? open[0]
}

export function incidentReason(kind: string): string {
  const reasons: Record<string, string> = {
    "credential-exposure-detected": "Credential exposure detected",
    "credential-metadata-stale": "Metadata needs verification",
    "credential-disabled": "Credential disabled or missing",
    "credential-provider-drift": "Provider settings changed",
    "credential-inventory-drift": "Inventory mapping changed",
    "credential-runtime-drift": "Runtime binding changed",
    "credential-expiring": "Credential nearing expiry",
    "credential-rotation-due": "Rotation due",
  }
  return reasons[kind] ?? titleCase(kind)
}

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
        if (location.protocol === "render:") return [platform]
        const environment = location.pathname.split("/").filter(Boolean).at(-1)
        return [platform + (environment && ["production", "development", "preview", "staging", "test"].includes(environment) ? " · " + titleCase(environment) : "")]
      } catch { return [] }
    })
    return { name: services.map(item => item.display_name).join(", "), detail: [...new Set(locations)].join(", ") || null }
  }
  return { name: credential.display_name, detail: credential.provider }
}
