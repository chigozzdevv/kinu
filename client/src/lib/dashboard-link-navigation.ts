import { createContext } from "react"
import type { NavItem } from "./navigation"

export const DashboardLinkNavigation = createContext<((section: NavItem) => void) | null>(null)
