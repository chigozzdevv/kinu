import { lazy, Suspense, useEffect, useState } from "react"
import type { Authentication } from "./lib/auth"
import kinuLogo from "./assets/kinu-logo.svg"
import { dashboardReturnKey, readAuthentication, redirectToSignIn, sessionExpiredEvent, watchSession } from "./lib/auth"
import { activateOrganisation, storedOrganisation } from "./lib/organisationstate"
import { api } from "./lib/api"
import type { AccountSession, OrganisationMembership } from "./types"
import { Button } from "./components/ui/button"
import { OrganisationSetupPage } from "./pages/organisation"
import { SignInPage } from "./pages/signin"
import { connectionCallbackIntegration, dashboardLocation } from "./lib/callback"

const HomePage = lazy(() => import("./landing/pages/home").then((module) => ({ default: module.HomePage })))
const Dashboard = lazy(() => import("./App.tsx"))

function dashboardReturnLocation() {
  const location = sessionStorage.getItem(dashboardReturnKey)
  sessionStorage.removeItem(dashboardReturnKey)
  return location?.startsWith("/dashboard") ? location : null
}

function LoadingScreen() {
  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)]"><img className="h-auto w-[132px]" src={kinuLogo} alt="Kinu" /></main>
}

export function AuthenticationBoundary() {
  const [authentication, setAuthentication] = useState<Authentication | null>(null)
  const [authError, setAuthError] = useState("")
  const identity = authentication?.session?.identity ?? null
  const [loading, setLoading] = useState(true)
  const [session, setSession] = useState<AccountSession | null>(null)
  const [active, setActive] = useState<OrganisationMembership | null>(null)
  const [sessionError, setSessionError] = useState("")

  useEffect(() => {
    let current = true
    const expired = () => setLoading(true)
    window.addEventListener(sessionExpiredEvent, expired)
    void readAuthentication().then((value) => {
      if (current) setAuthentication(value)
    }).catch((reason: unknown) => {
      if (current) setAuthError(reason instanceof Error ? reason.message : "Sign-in is unavailable")
    }).finally(() => { if (current) setLoading(false) })
    return () => { current = false; window.removeEventListener(sessionExpiredEvent, expired) }
  }, [])

  useEffect(() => {
    if (!authentication?.session || window.location.pathname === "/") return
    return watchSession(authentication.session.expires_at)
  }, [authentication?.session])

  useEffect(() => {
    if (!identity || window.location.pathname === "/") {
      setSession(null)
      setActive(null)
      setSessionError("")
      return
    }
    let current = true
    setSessionError("")
    void api.getSession().then((value) => {
      if (!current) return
      sessionStorage.removeItem(dashboardReturnKey)
      const callbackWorkspace = new URLSearchParams(window.location.search).get("workspace")
      const membership = (connectionCallbackIntegration() === "gmail" ? value.organisations.find((item) => item.organisation.id === callbackWorkspace) : undefined) ?? storedOrganisation(value.organisations)
      if (membership) activateOrganisation(membership)
      setSession(value)
      setActive(membership)
    }).catch((reason: unknown) => {
      if (current) setSessionError(reason instanceof Error ? reason.message : "Account could not be loaded")
    })
    return () => { current = false }
  }, [identity])

  if (loading) return <LoadingScreen />
  if (window.location.pathname === "/" && connectionCallbackIntegration()) {
    window.location.replace(dashboardLocation())
    return <LoadingScreen />
  }
  if (window.location.pathname === "/") return <Suspense fallback={<LoadingScreen />}><HomePage authenticated={Boolean(identity)} /></Suspense>
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
  if (sessionError) return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6"><div className="text-center"><p className="text-[12px] text-[var(--red)]">{sessionError}</p><Button className="mt-4" onClick={() => window.location.reload()}>Try again</Button></div></main>
  if (!session) return <LoadingScreen />
  if (!active) return <OrganisationSetupPage onCreated={(membership) => {
    activateOrganisation(membership)
    setSession({ organisations: [membership] })
    setActive(membership)
  }} />
  return <Suspense fallback={<LoadingScreen />}><Dashboard activeOrganisation={active} memberships={session.organisations} /></Suspense>
}
