import { useEffect, useState } from "react"
import { BrowserSetupSession, type BrowserSetupCapability } from "../connections/browser-session"

function capabilityFromFragment(): BrowserSetupCapability {
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const revision = Number(fragment.get("revision"))
  return {
    gatewayUrl: fragment.get("gateway_url") ?? "",
    organisationId: fragment.get("organisation_id") ?? "",
    revision: Number.isSafeInteger(revision) && revision >= 0 ? revision : null,
    setupId: fragment.get("setup_id") ?? "",
    token: fragment.get("token") ?? "",
    providerUrl: fragment.get("provider_url") ?? "",
  }
}

export function BrowserSetupPage() {
  const [capability] = useState(capabilityFromFragment)
  useEffect(() => {
    window.history.replaceState({}, "", window.location.pathname)
  }, [])
  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] p-5 text-[var(--ink)]"><div className="w-full max-w-[680px]"><BrowserSetupSession capability={capability} onComplete={() => window.close()} /></div></main>
}
