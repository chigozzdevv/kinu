import type { HostingPlatform, HostMapping, HostVariable } from "./api/hosting"
import type { Connection } from "../types"

export function isHostingPlatform(platform: string): platform is HostingPlatform {
  return platform === "render" || platform === "railway" || platform === "vercel"
}

export function variableLabel(variable: HostVariable) {
  return [variable.name, variable.service_name, variable.environment_name].filter(Boolean).join(" · ")
}

export function approvedProjects(connection: Connection, mappings: HostMapping[]) {
  const prefix = `${connection.platform}://`
  const names = new Map(mappings.flatMap(({ project }) => project ? [[project.id, project.name] as const] : []))
  return connection.allowed_resources.flatMap((resource) => {
    if (!resource.startsWith(prefix)) return []
    try {
      const id = decodeURIComponent(resource.slice(prefix.length))
      return id ? [{ id, name: names.get(id) }] : []
    } catch {
      return []
    }
  })
}

export function validHealthUrl(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === "https:" && !url.username && !url.password && !url.hash
  } catch {
    return false
  }
}
