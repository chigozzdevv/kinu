import { useEffect, useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Shell } from "./components/sidebar"
import { OverviewPage } from "./pages/overview"
import { CredentialsPage } from "./pages/credentials"
import { PlaybooksPage } from "./pages/playbooks"
import { IncidentsPage } from "./pages/incidents"
import { RotationsPage } from "./pages/rotations"
import { ApprovalsPage } from "./pages/approvals"
import { ConnectionsPage } from "./pages/connections"
import { connectionCallbackIntegration } from "./lib/callback"
import { AuditsPage } from "./pages/audits"
import { SettingsPage } from "./pages/settings"
import { BrowserSetupPage } from "./pages/browsersetup"
import { signOutIdentity } from "./lib/auth"
import { OrganisationProvider } from "./lib/organisation"
import { clearOrganisation } from "./lib/organisationstate"
import { dashboardPath, dashboardRoute, type DashboardRoute, type NavItem } from "./lib/navigation"
import type { OrganisationMembership } from "./types"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

export function App({
  activeOrganisation,
  memberships,
}: {
  activeOrganisation: OrganisationMembership
  memberships: OrganisationMembership[]
}) {
  const currentRoute = () => connectionCallbackIntegration()
    ? { section: "connections" as const }
    : dashboardRoute()
  const [route, setRoute] = useState<DashboardRoute>(currentRoute)
  const currentNav = route.section

  useEffect(() => {
    if (connectionCallbackIntegration() && window.location.pathname !== "/dashboard/connections") {
      window.history.replaceState({}, "", `/dashboard/connections${window.location.search}`)
    }
    const restoreRoute = () => setRoute(currentRoute())
    window.addEventListener("popstate", restoreRoute)
    return () => window.removeEventListener("popstate", restoreRoute)
  }, [])

  const navigate = (next: DashboardRoute, replace = false) => {
    const location = dashboardPath(next)
    if (`${window.location.pathname}${window.location.search}` !== location) {
      window.history[replace ? "replaceState" : "pushState"]({}, "", location)
    }
    setRoute(next)
  }

  const handleNavigate = (target: NavItem) => {
    navigate({ section: target })
  }

  const handleNavigateRotation = (runId: string) => {
    navigate({ section: "rotations", resourceId: runId })
  }

  const handleNavigateIncident = (incidentId: string) => {
    navigate({ section: "incidents", resourceId: incidentId })
  }

  const handleNavigateApproval = (approvalId: string) => {
    navigate({ section: "approvals", resourceId: approvalId })
  }

  const handleNavigateControls = (credentialId: string, controlVersionId: string) => {
    navigate({ section: "credentials", resourceId: credentialId, tab: "controls", controlVersionId })
  }

  const handleNavigateConnection = (connectionId: string) => {
    navigate({ section: "connections", resourceId: connectionId })
  }

  const handleLogout = async () => {
    try {
      await signOutIdentity()
    } finally {
      clearOrganisation()
      queryClient.clear()
      window.location.assign("/auth")
    }
  }

  if (window.location.pathname === "/browser/setup") {
    return <OrganisationProvider active={activeOrganisation} memberships={memberships}><QueryClientProvider client={queryClient}><BrowserSetupPage /></QueryClientProvider></OrganisationProvider>
  }
  const renderContent = () => {
    switch (currentNav) {
      case "overview":
        return <OverviewPage onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} onNavigateIncident={handleNavigateIncident} onNavigateApproval={handleNavigateApproval} />
      case "credentials":
        return <CredentialsPage key={dashboardPath(route)} initialCredentialId={route.resourceId} initialControlVersionId={route.controlVersionId} initialTab={route.tab} onSelectCredential={(credentialId, tab = "overview", controlVersionId) => navigate({ section: "credentials", resourceId: credentialId || undefined, tab, controlVersionId })} onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} />
      case "playbooks":
        return <PlaybooksPage />
      case "incidents":
        return <IncidentsPage key={dashboardPath(route)} initialIncidentId={route.resourceId} onSelectIncident={(incidentId) => navigate({ section: "incidents", resourceId: incidentId || undefined })} onNavigateRotation={handleNavigateRotation} />
      case "rotations":
        return <RotationsPage key={dashboardPath(route)} activeRunId={route.resourceId} onSelectRotation={(runId) => navigate({ section: "rotations", resourceId: runId || undefined })} onNavigateApproval={() => navigate({ section: "approvals" })} onNavigateControls={handleNavigateControls} onNavigateConnection={handleNavigateConnection} />
      case "approvals":
        return <ApprovalsPage key={dashboardPath(route)} initialApprovalId={route.resourceId} onSelectApproval={(approvalId) => navigate({ section: "approvals", resourceId: approvalId || undefined })} onNavigateRotation={handleNavigateRotation} />
      case "connections":
        return <ConnectionsPage key={dashboardPath(route)} initialConnectionId={route.resourceId} onSelectConnection={(connectionId) => navigate({ section: "connections", resourceId: connectionId || undefined })} />
      case "audits":
        return <AuditsPage />
      case "settings":
        return <SettingsPage />
      default:
        return <OverviewPage onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} onNavigateIncident={handleNavigateIncident} onNavigateApproval={handleNavigateApproval} />
    }
  }

  return (
    <OrganisationProvider active={activeOrganisation} memberships={memberships}>
      <QueryClientProvider client={queryClient}>
        <Shell currentNav={currentNav} onNavigate={handleNavigate} onLogout={() => { void handleLogout() }}>
          {renderContent()}
        </Shell>
      </QueryClientProvider>
    </OrganisationProvider>
  )
}

export default App
