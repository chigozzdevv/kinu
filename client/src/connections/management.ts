import type { Connection } from "../types"
import { api, type BrowserSetupResponse } from "../lib/api"

export async function openBrowserSetup(connectionId: string): Promise<BrowserSetupResponse> {
  const setup = await api.beginBrowserSetup(connectionId)
  let session = setup.session
  const expiresAt = Date.parse(setup.expires_at)
  while (session.status === "provisioning") {
    if (!Number.isFinite(expiresAt) || Date.now() >= expiresAt) {
      throw new Error("The browser setup session expired before it became ready.")
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2000))
    session = await api.getBrowserSetup(session.id)
  }
  if (session.status !== "ready") {
    throw new Error("The browser setup did not become available. Try again.")
  }
  return { ...setup, session }
}

export function requiresBrowserAuthentication(connection: Connection) {
  if (connection.interface !== "browser") return false
  if (connection.status !== "ready" || !connection.authorization_reference) return true
  if (!connection.authorization_expires_at) return false
  const expiresAt = Date.parse(connection.authorization_expires_at)
  return !Number.isFinite(expiresAt) || expiresAt <= Date.now()
}

export function disconnectImpact(connection: Connection) {
  if (connection.platform === "email") return "Stops processing alerts sent to this workspace’s email address."
  if (connection.platform === "gmail") return "Stops Gmail monitoring and removes saved access for this workspace."
  if (connection.platform === "github") return "Removing this connection will disable GitHub security monitoring for exposed credentials."
  if (connection.interface === "browser") return `Removing this connection will stop Kinu from rotating credentials through ${connection.display_name} with Computer Use.`
  if (connection.roles.includes("provider")) return `Removing this connection will stop Kinu from creating, testing, and revoking credentials through ${connection.display_name}.`
  if (connection.roles.includes("secret-store")) return `Removing this connection will stop Kinu from securely storing and retrieving replacement credentials through ${connection.display_name}.`
  if (connection.roles.includes("runtime")) return `Removing this connection will stop Kinu from deploying and verifying credential rotations through ${connection.display_name}.`
  if (connection.roles.includes("telemetry")) return `Removing this connection will stop Kinu from verifying service health through ${connection.display_name}.`
  return "Removing this connection will disable the Kinu operations that depend on it."
}
