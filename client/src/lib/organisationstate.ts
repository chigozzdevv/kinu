import type { OrganisationMembership } from "../types"
import { setActiveOrganisationId } from "./api"

const selectionKey = "kinu.organisation"

export function storedOrganisation(memberships: OrganisationMembership[]) {
  const stored = sessionStorage.getItem(selectionKey)
  return memberships.find((item) => item.organisation.id === stored) ?? memberships[0] ?? null
}

export function activateOrganisation(membership: OrganisationMembership) {
  setActiveOrganisationId(membership.organisation.id)
  sessionStorage.setItem(selectionKey, membership.organisation.id)
}

export function clearOrganisation() {
  sessionStorage.removeItem(selectionKey)
}
