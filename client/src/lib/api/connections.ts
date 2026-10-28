import type { Connection, Identifier, ProviderCredentialMetadata, RuntimeResourceMetadata } from "../../types"
import type { CreateConnectionInput, SecretResourceMetadata, SecretVersionMetadata, BrowserSetupResponse, BrowserSetupSession, GitHubOnboardingResponse, GitHubDiscoveryResponse, GitHubCompletionResponse } from "./types"
import { request, archive, workspacePath } from "./http"

export const connections = {
async createConnection(input: CreateConnectionInput): Promise<Connection> {
    const created = await request<Connection>(`${workspacePath()}/inventory/connections`, {
      method: "POST",
      body: JSON.stringify(input.connection),
    })
    if (!input.playbook_id || !input.playbook_version_id) return created
    return this.attachPlaybook(created, input.playbook_id, input.playbook_version_id)
  },

async getConnection(connectionId: Identifier): Promise<Connection> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}`)
  },

async resolveCredential(connectionId: Identifier, input: { secret_store_connection_id: Identifier; secret_reference: string }): Promise<ProviderCredentialMetadata> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}/resolve-credential`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

async getRuntimeResources(connectionId: Identifier): Promise<RuntimeResourceMetadata[]> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}/runtime-resources`)
  },

async getSecretResources(connectionId: Identifier): Promise<SecretResourceMetadata[]> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}/secret-resources`)
  },

async getSecretVersions(connectionId: Identifier, secret: string): Promise<SecretVersionMetadata[]> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}/secret-versions?secret=${encodeURIComponent(secret)}`)
  },

async updateConnection(connectionId: Identifier, input: { expected_revision: number; display_name?: string; capabilities?: string[]; allowed_resources?: string[]; region?: string }): Promise<Connection> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  },

async archiveConnection(connectionId: Identifier, expectedRevision: number): Promise<Connection> {
    return archive(`${workspacePath()}/inventory/connections/${connectionId}`, expectedRevision)
  },

async attachPlaybook(connection: Connection, playbookId: Identifier, versionId: Identifier): Promise<Connection> {
    return request(`${workspacePath()}/playbooks/${playbookId}/versions/${versionId}/attach`, {
      method: "POST",
      body: JSON.stringify({ connection_id: connection.id, expected_revision: connection.revision }),
    })
  },

async beginBrowserSetup(connectionId: Identifier): Promise<BrowserSetupResponse> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}/setup`, {
      method: "POST",
      body: JSON.stringify({ extra_domains: [] }),
    })
  },

async getBrowserSetup(setupId: Identifier): Promise<BrowserSetupSession> {
    return request(`${workspacePath()}/inventory/setups/${setupId}`)
  },

async beginGitHubOnboarding(): Promise<GitHubOnboardingResponse> {
    return request(`${workspacePath()}/github/onboarding`, { method: "POST", body: JSON.stringify({}) })
  },

async discoverGitHubOnboarding(
    sessionId: Identifier,
    input: { state: string; pkce_verifier: string; code: string; installation_id?: number },
  ): Promise<GitHubDiscoveryResponse> {
    return request(`${workspacePath()}/github/onboarding/${sessionId}/discover`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

async completeGitHubOnboarding(
    sessionId: Identifier,
  ): Promise<GitHubCompletionResponse> {
    return request(`${workspacePath()}/github/onboarding/${sessionId}/complete`, {
      method: "POST",
      body: JSON.stringify({}),
    })
  },

async completeBrowserSetup(setupId: Identifier, expectedRevision: number, token: string): Promise<{ connection: Connection }> {
    return request(`${workspacePath()}/inventory/setups/${setupId}/complete`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, token }),
    })
  },

async abortBrowserSetup(setupId: Identifier, expectedRevision: number): Promise<void> {
    await request(`${workspacePath()}/inventory/setups/${setupId}/abort`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    })
  },

async getConnections(): Promise<Connection[]> {
    return request(`${workspacePath()}/inventory/connections`)
  }
}

