import { lazy, Suspense, useEffect, useState } from "react"
import type { Authentication } from "./lib/auth"
import kinuLogo from "./assets/kinu-logo.svg"
import { dashboardReturnKey, readAuthentication, redirectToSignIn, sessionExpiredEvent, watchSession } from "./lib/auth"
import { activateOrganisation, storedOrganisation } from "./lib/organisationstate"
import { api } from "./lib/api"
import type { AccountSession, OrganisationMembership } from "./types"
import { Button } from "./components/ui/button"
import { OrganisationSetupPage } from "./pages/organisation"
import { claimInstallation, setupKey } from "./lib/setup"
import { SignInPage } from "./pages/signin"
import { connectionCallbackIntegration, dashboardLocation } from "./lib/callback"

const HomePage = lazy(() => import("./pages/home"))
const PublicInfoPage = lazy(() => import("./pages/public-info"))
const Dashboard = lazy(() => import("./App.tsx"))
const publicPages: Readonly<Record<string, "vercel" | "terms" | "privacy" | undefined>> = {
  "/docs/vercel": "vercel", "/terms": "terms", "/privacy": "privacy",
}

function dashboardReturnLocation() {
  const location = sessionStorage.getItem(dashboardReturnKey)
  sessionStorage.removeItem(dashboardReturnKey)
  return location?.startsWith("/dashboard") ? location : null
}

function LoadingScreen() {
  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)]"><img className="h-auto w-[132px]" src={kinuLogo} alt="Kinu" /></main>
}

export function AuthenticationBoundary() {
  const publicPage = publicPages[window.location.pathname]
  const [authentication, setAuthentication] = useState<Authentication | null>(null)
  const [authError, setAuthError] = useState("")
  const identity = authentication?.session?.identity ?? null
  const [loading, setLoading] = useState(true)
  const [session, setSession] = useState<AccountSession | null>(null)
  const [active, setActive] = useState<OrganisationMembership | null>(null)
  const [sessionError, setSessionError] = useState("")

  useEffect(() => {
    if (publicPage) return
    let current = true
    const expired = () => setLoading(true)
    window.addEventListener(sessionExpiredEvent, expired)
    void readAuthentication().then((value) => {
      if (current) setAuthentication(value)
    }).catch((reason: unknown) => {
      if (current) setAuthError(reason instanceof Error ? reason.message : "Sign-in is unavailable")
    }).finally(() => { if (current) setLoading(false) })
    return () => { current = false; window.removeEventListener(sessionExpiredEvent, expired) }
  }, [publicPage])

  useEffect(() => {
    if (publicPage || !authentication?.session || window.location.pathname === "/") return
    return watchSession(authentication.session.expires_at)
  }, [authentication?.session, publicPage])

  useEffect(() => {
    if (publicPage || !identity || window.location.pathname === "/") {
      setSession(null)
      setActive(null)
      setSessionError("")
      return
    }
    let current = true
    setSessionError("")
    void claimInstallation().then(() => api.getSession()).then((value) => {
      if (!current) return
      sessionStorage.removeItem(dashboardReturnKey)
      const callbackWorkspace = new URLSearchParams(window.location.search).get("workspace")
      const membership = (connectionCallbackIntegration() ? value.organisations.find((item) => item.organisation.id === callbackWorkspace) : undefined) ?? storedOrganisation(value.organisations)
      if (membership) activateOrganisation(membership)
      setSession(value)
      setActive(membership)
    }).catch((reason: unknown) => {
      if (current) setSessionError(reason instanceof Error ? reason.message : "Account could not be loaded")
    })
    return () => { current = false }
  }, [identity, publicPage])

  if (publicPage) return <Suspense fallback={<LoadingScreen />}><PublicInfoPage page={publicPage} /></Suspense>

  if (window.location.pathname === "/" && connectionCallbackIntegration()) {
    window.location.replace(dashboardLocation())
    return <LoadingScreen />
  }
  if (window.location.pathname === "/") return <Suspense fallback={<LoadingScreen />}><HomePage authenticated={Boolean(identity)} /></Suspense>
  if (loading) return <LoadingScreen />
  if (authError) return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6"><div className="text-center"><p role="alert" className="text-[12px] text-[var(--red)]">{authError}</p><Button className="mt-4" onClick={() => window.location.reload()}>Try again</Button></div></main>
  if (window.location.pathname === "/auth") {
    if (identity) {
      const returnLocation = dashboardReturnLocation()
      window.location.replace(connectionCallbackIntegration() ? dashboardLocation() : returnLocation ?? dashboardLocation())
      return <LoadingScreen />
    }
    return <SignInPage configured={authentication?.configured ?? false} returnTo={sessionStorage.getItem(dashboardReturnKey) ?? "/dashboard"} />
  }
  if (!identity) {
    redirectToSignIn(false)
    return <LoadingScreen />
  }
  if (sessionError) return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6"><div className="text-center"><p className="text-[12px] text-[var(--red)]">{sessionError}</p>{sessionStorage.getItem(setupKey) && <Button className="mt-4 mr-2" variant="secondary" onClick={() => { sessionStorage.removeItem(setupKey); window.location.reload() }}>Cancel setup</Button>}<Button className="mt-4" onClick={() => window.location.reload()}>Try again</Button></div></main>
  if (!session) return <LoadingScreen />
  if (!active) return <OrganisationSetupPage onCreated={(membership) => {
    activateOrganisation(membership)
    setSession({ organisations: [membership] })
    setActive(membership)
  }} />
  return <Suspense fallback={<LoadingScreen />}><Dashboard activeOrganisation={active} memberships={session.organisations} /></Suspense>
}
