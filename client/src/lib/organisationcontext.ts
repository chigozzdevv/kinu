import { createContext } from "react"
import type { OrganisationMembership } from "../types"

export type OrganisationContextValue = {
  active: OrganisationMembership
  memberships: OrganisationMembership[]
  select: (organisationId: string) => void
}

export const OrganisationContext = createContext<OrganisationContextValue | null>(null)
