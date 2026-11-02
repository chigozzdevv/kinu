import type { IntegrationKind } from "../components/integration"
import type { ControlValues } from "./controls"

export interface HostKeyForm {
  project: string
  variable: string
  provider: string
  choices: Record<string, string>
  health: Record<string, string>
  createReference: string
  scopes: string
  controls: ControlValues
  step: number
}

export interface AwsKeyForm {
  connectionId: string
  secretStoreId: string
  secretResource: string
  secretReference: string
  runtimeConnectionId: string
  runtimeResource: string
  runtimeSecretName: string
  runtimeContainerName: string | null
  environmentName: string
  controls: ControlValues
  step: number
}

export type CredentialDraft =
  | { source: "hosting"; hostId: string; form?: HostKeyForm }
  | { source: "aws"; form: AwsKeyForm }

const returnKey = "kinu.credential-connection"

export function saveCredentialReturn(organisation: string, integration: IntegrationKind, draft: CredentialDraft) {
  sessionStorage.setItem(returnKey, JSON.stringify({
    organisation, integration, draft, expiresAt: Date.now() + 60 * 60 * 1000,
  }))
}

export function credentialReturn(organisation: string, integration: IntegrationKind | null): CredentialDraft | null {
  if (!integration) return null
  const saved = sessionStorage.getItem(returnKey)
  if (!saved) return null
  try {
    const value = JSON.parse(saved)
    const workspace = new URLSearchParams(window.location.search).get("workspace")
    if (value.organisation !== organisation || (workspace && workspace !== organisation)
      || value.integration !== integration || !(value.expiresAt > Date.now())) return null
    const draft = value.draft
    if (draft?.source === "hosting" && typeof draft.hostId === "string") return draft
    if (draft?.source === "aws" && draft.form?.controls) return draft
  } catch {
    clearCredentialReturn()
  }
  return null
}

export function clearCredentialReturn() {
  sessionStorage.removeItem(returnKey)
}

export function finishCredentialConnection() {
  clearCredentialReturn()
  window.history.replaceState({}, "", "/dashboard/credentials?add=1")
}
