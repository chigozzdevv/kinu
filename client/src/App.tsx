import { useEffect, useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Shell } from "./components/sidebar"
import { DashboardLinkNavigation } from "./lib/dashboard-link-navigation"
import { OverviewPage } from "./pages/overview"
import { CredentialsPage } from "./pages/credentials"
import { CredentialOnboarding } from "./pages/credential-onboarding"
import { PlaybooksPage } from "./pages/playbooks"
import { IncidentsPage } from "./pages/incidents"
import { RotationsPage } from "./pages/rotations"
import { ApprovalsPage } from "./pages/approvals"
import { ConnectionsPage } from "./pages/connections"
import { connectionCallbackIntegration } from "./lib/callback"
import { clearCredentialReturn, credentialReturn } from "./lib/credential-return"
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

function currentRoute(organisation: string): DashboardRoute {
  const integration = connectionCallbackIntegration()
  if (!integration) return dashboardRoute()
  return credentialReturn(organisation, integration)
    ? { section: "credentials", addCredential: true }
    : { section: "connections" }
}

export function App({
  activeOrganisation,
  memberships,
}: {
  activeOrganisation: OrganisationMembership
  memberships: OrganisationMembership[]
}) {
  const organisation = activeOrganisation.organisation.id
  const [route, setRoute] = useState<DashboardRoute>(() => currentRoute(organisation))
  const [logoutError, setLogoutError] = useState("")
  const currentNav = route.section

  useEffect(() => {
    if (connectionCallbackIntegration()) {
      const callback = currentRoute(organisation)
      const params = new URLSearchParams(window.location.search)
      if (callback.addCredential) params.set("add", "1")
      window.history.replaceState({}, "", `/dashboard/${callback.section}?${params}`)
    }
    const restoreRoute = () => setRoute(currentRoute(organisation))
    window.addEventListener("popstate", restoreRoute)
    return () => window.removeEventListener("popstate", restoreRoute)
  }, [organisation])

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
      const destination = await signOutIdentity()
      clearCredentialReturn()
      clearOrganisation()
      queryClient.clear()
      window.location.assign(destination)
    } catch (reason) {
      setLogoutError(reason instanceof Error ? reason.message : "Sign-out could not be completed")
    }
  }

  if (window.location.pathname === "/browser/setup") {
    return <OrganisationProvider active={activeOrganisation} memberships={memberships}><QueryClientProvider client={queryClient}><BrowserSetupPage /></QueryClientProvider></OrganisationProvider>
  }
  const renderContent = () => {
    switch (currentNav) {
      case "overview":
        return <OverviewPage onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} onNavigateIncident={handleNavigateIncident} onNavigateApproval={handleNavigateApproval} onNavigateAi={() => navigate({ section: "settings", tab: "ai" })} />
      case "credentials":
        if (route.addCredential) return <CredentialOnboarding onFinish={(id) => navigate({ section: "credentials", resourceId: id || undefined })} />
        return <CredentialsPage key={dashboardPath(route)} onAdd={() => navigate({ section: "credentials", addCredential: true })} initialCredentialId={route.resourceId} initialControlVersionId={route.controlVersionId} initialTab={route.tab} onSelectCredential={(credentialId, tab = "overview", controlVersionId) => navigate({ section: "credentials", resourceId: credentialId || undefined, tab, controlVersionId })} onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} />
      case "playbooks":
        return <PlaybooksPage key={dashboardPath(route)} initialPlaybookId={route.resourceId} onSelectPlaybook={(playbookId) => navigate({ section: "playbooks", resourceId: playbookId || undefined })} />
      case "incidents":
        return <IncidentsPage key={dashboardPath(route)} initialIncidentId={route.resourceId} onSelectIncident={(incidentId) => navigate({ section: "incidents", resourceId: incidentId || undefined })} onNavigateRotation={handleNavigateRotation} onAddCredential={() => navigate({ section: "credentials", addCredential: true })} onConnectSource={() => navigate({ section: "connections" })} />
      case "rotations":
        return <RotationsPage key={dashboardPath(route)} activeRunId={route.resourceId} onSelectRotation={(runId) => navigate({ section: "rotations", resourceId: runId || undefined })} onNavigateApproval={() => navigate({ section: "approvals" })} onNavigateControls={handleNavigateControls} onNavigateConnection={handleNavigateConnection} onNavigateIncident={handleNavigateIncident} onNavigateAi={() => navigate({ section: "settings", tab: "ai" })} />
      case "approvals":
        return <ApprovalsPage key={dashboardPath(route)} initialApprovalId={route.resourceId} onSelectApproval={(approvalId) => navigate({ section: "approvals", resourceId: approvalId || undefined })} onNavigateRotation={handleNavigateRotation} />
      case "connections":
        return <ConnectionsPage key={dashboardPath(route)} initialConnectionId={route.resourceId} onSelectConnection={(connectionId) => navigate({ section: "connections", resourceId: connectionId || undefined })} />
      case "audits":
        return <AuditsPage />
      case "settings":
        return <SettingsPage key={dashboardPath(route)} />
      default:
        return <OverviewPage onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} onNavigateIncident={handleNavigateIncident} onNavigateApproval={handleNavigateApproval} onNavigateAi={() => navigate({ section: "settings", tab: "ai" })} />
    }
  }

  return (
    <OrganisationProvider active={activeOrganisation} memberships={memberships}>
      <QueryClientProvider client={queryClient}>
        <Shell currentNav={currentNav} onNavigate={handleNavigate} onLogout={() => { void handleLogout() }}>
          {logoutError && <p role="alert" className="px-6 py-3 text-[12px] text-[var(--red)]">{logoutError}</p>}
          <DashboardLinkNavigation value={handleNavigate}>{renderContent()}</DashboardLinkNavigation>
        </Shell>
      </QueryClientProvider>
    </OrganisationProvider>
  )
}

export default App
