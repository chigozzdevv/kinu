import { useEffect, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Detail, DetailList, Section } from "../components/detail"
import { IntegrationMark } from "../components/integration"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { ConnectPage, SetupPage } from "../components/workspace"
import type { Connection } from "../types"
import { ApiError, api, type GitHubDiscoveryResponse, type GitHubOnboardingResponse } from "../lib/api"
import { authorizeConnection, connectionCallbackIntegration, type BeforeAuthorization } from "../lib/callback"
import { disconnectImpact } from "./management"
import type { ProviderSetupProps } from "./provider-setup-props"

type SavedGitHubOnboarding = GitHubOnboardingResponse & { installation_id?: number }

export function GitHubSetup({ onClose, onBack, onChanged, onCreated, connections, beforeAuthorization }: ProviderSetupProps & { connections: Connection[]; beforeAuthorization?: BeforeAuthorization }) {
  const [step, setStep] = useState(0)
  const [discovery, setDiscovery] = useState<GitHubDiscoveryResponse | null>(null)
  const [callbackError, setCallbackError] = useState("")
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const githubConnection = connections.find((item) => item.platform === "github" && item.roles.includes("incident") && !item.archived_at && item.status !== "disabled")
  const begin = useMutation({
    mutationFn: () => api.beginGitHubOnboarding(),
    onSuccess: (value) => {
      sessionStorage.setItem("kinu.github", JSON.stringify(value))
      authorizeConnection(value.authorization_url, "github", beforeAuthorization)
    },
  })
  const install = useMutation({
    mutationFn: () => api.beginGitHubOnboarding(),
    onSuccess: (value) => {
      sessionStorage.setItem("kinu.github", JSON.stringify(value))
      authorizeConnection(value.installation_url, "github", beforeAuthorization)
    },
  })
  const discover = useMutation({
    mutationFn: ({ saved, code, state, installationId }: { saved: SavedGitHubOnboarding; code: string; state: string; installationId?: number }) => api.discoverGitHubOnboarding(saved.session.id, { state, pkce_verifier: saved.pkce_verifier, code, installation_id: installationId }),
  })
  const complete = useMutation({ mutationFn: async () => {
    if (!discovery) throw new Error("GitHub repositories are unavailable")
    const completed = await api.completeGitHubOnboarding(discovery.session.id)
    return api.getConnection(`conn_github_${completed.installation.installation_id}`)
  } })

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search)
    const callback = parameters.has("github") || parameters.has("installation_id") || (parameters.has("code") && sessionStorage.getItem("kinu.github"))
    if (!callback) return
    const clearCallback = () => window.history.replaceState({}, "", beforeAuthorization ? "/dashboard/credentials?add=1" : window.location.pathname)
    const raw = sessionStorage.getItem("kinu.github")
    if (!raw) {
      clearCallback()
      setCallbackError("GitHub connection session is unavailable")
      return
    }
    try {
      const saved = JSON.parse(raw) as SavedGitHubOnboarding
      const rawInstallationId = parameters.get("installation_id") ?? saved.installation_id
      const installationId = rawInstallationId === null || rawInstallationId === undefined ? undefined : Number(rawInstallationId)
      const code = parameters.get("code")
      const state = parameters.get("state")
      if (installationId !== undefined && (!Number.isInteger(installationId) || installationId <= 0)) {
        sessionStorage.removeItem("kinu.github")
        clearCallback()
        setCallbackError("GitHub did not return an installation")
        return
      }
      if (!code) {
        if (installationId === undefined) {
          sessionStorage.removeItem("kinu.github")
          clearCallback()
          setCallbackError("GitHub authorization is incomplete")
          return
        }
        const continued = { ...saved, installation_id: installationId }
        sessionStorage.setItem("kinu.github", JSON.stringify(continued))
        clearCallback()
        authorizeConnection(saved.authorization_url, "github", beforeAuthorization)
        return
      }
      if (!state) {
        sessionStorage.removeItem("kinu.github")
        clearCallback()
        setCallbackError("GitHub authorization is incomplete")
        return
      }
      sessionStorage.removeItem("kinu.github")
      clearCallback()
      discover.mutate({ saved, code, state, installationId }, {
        onSuccess: (value) => {
          setDiscovery(value)
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === "setup-required") {
            install.mutate()
          }
        },
      })
    } catch {
      sessionStorage.removeItem("kinu.github")
      clearCallback()
      setCallbackError("GitHub connection session is invalid")
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const disconnect = useMutation({
    mutationFn: () => {
      if (!githubConnection) throw new Error("GitHub connection is unavailable")
      return api.archiveConnection(githubConnection.id, githubConnection.revision)
    },
    onSuccess: async () => {
      setConfirmingDisconnect(false)
      await onChanged()
      onClose()
    },
  })

  async function submit() {
    const connection = await complete.mutateAsync()
    await onChanged()
    if (onCreated) await onCreated(connection)
    else onClose()
  }

  if (!discovery) {
    const returning = Boolean(sessionStorage.getItem("kinu.github") && connectionCallbackIntegration() === "github")
    const busy = begin.isPending || install.isPending || discover.isPending || returning
    if (githubConnection && !returning) return <><ConnectPage eyebrow="Inventory / Connections" title="GitHub" onBack={onBack} onClose={onClose} error={callbackError || begin.error?.message || install.error?.message || discover.error?.message || disconnect.error?.message} action={<>{githubConnection.status !== "ready" && <Button disabled={busy} onClick={() => begin.mutate()}>{busy ? "Connecting…" : "Reconnect"}</Button>}<Button variant="secondary" onClick={() => setConfirmingDisconnect(true)}>Disconnect</Button></>}><IntegrationMark kind="github" /></ConnectPage><Modal isOpen={confirmingDisconnect} onClose={() => setConfirmingDisconnect(false)} title="Disconnect GitHub?" description={disconnectImpact(githubConnection)} size="narrow" showClose={false} actions={<Button variant="danger" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>{disconnect.isPending ? "Disconnecting…" : "Disconnect"}</Button>} /></>
    return <ConnectPage eyebrow="Inventory / Connections" title="GitHub" onBack={onBack} onClose={onClose} error={callbackError || begin.error?.message || install.error?.message || discover.error?.message} action={<Button onClick={() => begin.mutate()} disabled={busy}>{busy ? "Connecting…" : "Connect"}</Button>}><IntegrationMark kind="github" /></ConnectPage>
  }

  return <SetupPage eyebrow="Inventory / Connections" title="GitHub" steps={["Repositories", "Review"]} current={step} onBack={() => setStep(0)} onExit={onBack} onCancel={onClose} error={complete.error?.message} primary={step === 0 ? <Button onClick={() => setStep(1)}>Continue</Button> : <Button onClick={submit} disabled={complete.isPending}>{complete.isPending ? "Connecting…" : "Connect"}</Button>}>
    {step === 0 && <div className="overflow-hidden rounded-xl border border-[var(--border)]">
      <div className="grid grid-cols-[minmax(0,1fr)_140px] gap-4 border-b border-[var(--border-soft)] px-4 py-3 text-[9px] font-semibold text-[var(--ink-muted)]"><span>Repository</span><span>Status</span></div>
      {discovery.repositories.map((repository) => <div key={repository.repository_id} className="grid grid-cols-[minmax(0,1fr)_140px] items-center gap-4 border-b border-[var(--border-soft)] px-4 py-3 last:border-b-0"><div className="truncate text-[11px] font-semibold text-[var(--ink)]">{repository.full_name}</div><span className="text-[10px] text-[var(--ink-soft)]">{repository.secret_scanning === "enabled" ? "Ready" : "Action required"}</span></div>)}
    </div>}
    {step === 1 && <div className="grid gap-5 lg:grid-cols-2"><Section title="GitHub" onEdit={() => setStep(0)}><DetailList><Detail label="Account">{discovery.installation.account_login}</Detail><Detail label="Repositories">{discovery.repositories.length}</Detail></DetailList></Section><Section title="Access" onEdit={() => setStep(0)}><DetailList><Detail label="Webhook">{discovery.installation.ready ? "Verified" : "Action required"}</Detail><Detail label="Secret scanning">{discovery.repositories.every((item) => item.secret_scanning === "enabled") ? "Ready" : "Action required"}</Detail></DetailList></Section></div>}
  </SetupPage>
}
