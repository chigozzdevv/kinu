import type { ReactNode } from "react"
import type { OrganisationMembership } from "../types"
import { OrganisationContext } from "./organisationcontext"
import { activateOrganisation } from "./organisationstate"
import { dashboardPath, dashboardRoute } from "./navigation"

export function OrganisationProvider({
  active,
  memberships,
  children,
}: {
  active: OrganisationMembership
  memberships: OrganisationMembership[]
  children: ReactNode
}) {
  const select = (organisationId: string) => {
    const membership = memberships.find((item) => item.organisation.id === organisationId)
    if (!membership || membership.organisation.id === active.organisation.id) return
    activateOrganisation(membership)
    const route = dashboardRoute()
    window.history.replaceState({}, "", dashboardPath({ section: route.section, tab: route.tab }))
    window.location.reload()
  }

  return (
    <OrganisationContext.Provider value={{ active, memberships, select }}>
      {children}
    </OrganisationContext.Provider>
  )
}
