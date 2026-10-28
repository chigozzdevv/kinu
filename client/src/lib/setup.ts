import { sessionFetch } from "./auth.ts"

export const setupKey = "kinu.setup"

export function captureSetupLink() {
  if (window.location.pathname !== "/auth") return
  const token = new URLSearchParams(window.location.hash.slice(1)).get("setup")
  if (!token || !/^[a-f0-9]{96}$/.test(token)) return
  sessionStorage.setItem(setupKey, token)
  window.history.replaceState({}, "", window.location.pathname + window.location.search)
}

export async function claimInstallation() {
  const token = sessionStorage.getItem(setupKey)
  if (!token) return
  const response = await sessionFetch("/v1/installation/claim", {
    method: "POST", body: JSON.stringify({ token }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { message?: string } | null
    throw new Error(body?.message ?? "Installation setup could not be completed")
  }
  sessionStorage.removeItem(setupKey)
}
