export type NavItem =
  | "overview"
  | "credentials"
  | "incidents"
  | "rotations"
  | "approvals"
  | "playbooks"
  | "connections"
  | "audits"
  | "settings"

export type DashboardRoute = {
  section: NavItem
  resourceId?: string
  tab?: string
  controlVersionId?: string
}

const sections = new Set<NavItem>([
  "credentials",
  "incidents",
  "rotations",
  "approvals",
  "playbooks",
  "connections",
  "audits",
  "settings",
])

const detailSections = new Set<NavItem>([
  "credentials",
  "incidents",
  "rotations",
  "approvals",
  "connections",
])

function decoded(value: string | undefined) {
  if (!value) return undefined
  try {
    return decodeURIComponent(value)
  } catch {
    return undefined
  }
}

export function dashboardRoute(pathname = window.location.pathname, search = window.location.search): DashboardRoute {
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean)
  if (parts[0] !== "dashboard" || !parts[1] || !sections.has(parts[1] as NavItem)) return { section: "overview" }

  const section = parts[1] as NavItem
  const resourceId = detailSections.has(section) ? decoded(parts[2]) : undefined
  if (section !== "credentials" || !resourceId) return { section, resourceId }

  const parameters = new URLSearchParams(search)
  return {
    section,
    resourceId,
    tab: parameters.get("tab") ?? undefined,
    controlVersionId: parameters.get("version") ?? undefined,
  }
}

export function dashboardPath(route: DashboardRoute): string {
  let path = route.section === "overview" ? "/dashboard" : `/dashboard/${route.section}`
  if (route.resourceId && detailSections.has(route.section)) path += `/${encodeURIComponent(route.resourceId)}`
  if (route.section !== "credentials" || !route.resourceId) return path

  const parameters = new URLSearchParams()
  if (route.tab && route.tab !== "overview") parameters.set("tab", route.tab)
  if (route.controlVersionId && route.tab === "controls") parameters.set("version", route.controlVersionId)
  const query = parameters.toString()
  return query ? `${path}?${query}` : path
}
