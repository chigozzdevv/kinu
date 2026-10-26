import { useContext } from "react"
import { OrganisationContext } from "./organisationcontext"

export function useOrganisation() {
  const value = useContext(OrganisationContext)
  if (!value) throw new Error("Organisation context is unavailable")
  return value
}
