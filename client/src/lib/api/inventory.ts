import type { Environment, Identifier, InventoryGraph, ManagedCredential } from "../../types"
import type { ImportCredentialInput, ControlPreferences, ControlVersion, CredentialControlsResponse } from "./types"
import { request, archive, workspacePath } from "./http"

export const inventory = {
  async selectCredentialBrowser(credentialId: Identifier, connectionId: Identifier, expectedRevision: number): Promise<ManagedCredential> {
    return request(`${workspacePath()}/connections/provider/credentials/${credentialId}/browser`, {
      method: "POST", body: JSON.stringify({ connection_id: connectionId, expected_revision: expectedRevision }),
    })
  },
  async getGraph(): Promise<InventoryGraph> {
    return request(`${workspacePath()}/inventory/graph`)
  },

  async getCredential(credentialId: Identifier): Promise<ManagedCredential> {
    return request(`${workspacePath()}/inventory/credentials/${credentialId}`)
  },

  async updateCredential(credentialId: Identifier, input: { expected_revision: number; display_name?: string }): Promise<ManagedCredential> {
    return request(`${workspacePath()}/inventory/credentials/${credentialId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  },

  async archiveCredential(credentialId: Identifier, expectedRevision: number): Promise<ManagedCredential> {
    return archive(`${workspacePath()}/inventory/credentials/${credentialId}`, expectedRevision)
  },

  async importCredential(input: ImportCredentialInput): Promise<ManagedCredential> {
    return request(`${workspacePath()}/inventory/credentials`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

  async getCredentialControls(credentialId: Identifier, versionId: Identifier): Promise<ControlVersion> {
    return request(`${workspacePath()}/inventory/credentials/${credentialId}/controls/${versionId}`)
  },

  async updateCredentialControls(
    credentialId: Identifier,
    input: { expected_revision: number; version_id: Identifier; controls: ControlPreferences },
  ): Promise<CredentialControlsResponse> {
    return request(`${workspacePath()}/inventory/credentials/${credentialId}/controls`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

  async getEnvironments(): Promise<Environment[]> {
    return request(`${workspacePath()}/inventory/environments`)
  }
}

