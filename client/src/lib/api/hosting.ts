import type { Connection, ManagedCredential, ProviderCredentialMetadata } from "../../types"
import type { ControlPreferences } from "./types"
import { request, workspacePath } from "./http"

export type HostingPlatform = "vercel" | "railway" | "render"
export interface HostProject { id: string; name: string }
export interface HostMapping { reference: string; credential_id: string; service: { id: string; revision: number; endpoint: string | null } }
export interface KeySetup {
  id: string; reference: string; project_id: string; credential_id: string | null; message: string | null;
  phase: "pending" | "creating" | "stored" | "writing" | "deploying" | "registering" | "completed" | "cancelled" | "review-required" | "recovering" | "restoring";
}
export interface HostVariable {
  reference: string; project_id: string; environment: string; service_id: string | null;
  name: string; key_id: string; readable: boolean; environment_name: string | null; service_name: string | null;
}
export interface HostProjects {
  platform: HostingPlatform; display_name: string; connection: Connection | null;
  projects: HostProject[]; selected: string[];
}
const path = () => `${workspacePath()}/connections/hosting`

export const hosting = {
  async getKeySetups(connectionId: string): Promise<KeySetup[]> {
    return request(`${path()}/${connectionId}/key-setups`)
  },
  async createManagedKey(connectionId: string, requestId: string, input: { project_id: string; variable_reference: string; provider_connection_id: string; health_url: string; scopes: string[]; initial_controls: ControlPreferences }): Promise<KeySetup> {
    return request(`${path()}/${connectionId}/key-setups`, {
      method: "POST", headers: { "Idempotency-Key": requestId }, body: JSON.stringify(input),
    })
  },
  async recoverKeySetup(connectionId: string, setupId: string): Promise<KeySetup> {
    return request(`${path()}/${connectionId}/key-setups/${setupId}/recover`, { method: "POST", body: "{}" })
  },
  async getHostMappings(connectionId: string): Promise<HostMapping[]> {
    return request(`${path()}/${connectionId}/credentials`)
  },
  async saveHostHealth(service: HostMapping["service"], endpoint: string): Promise<void> {
    await request(`${workspacePath()}/inventory/services/${service.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: service.revision, endpoint }) })
  },
  async getProviderKeys(connectionId: string): Promise<ProviderCredentialMetadata[]> {
    return request(`${workspacePath()}/inventory/connections/${connectionId}/credential-metadata`)
  },
  async importHostKeys(connectionId: string, projectId: string, mappings: { variable_reference: string; provider_connection_id: string; provider_id: string; health_url: string | null }[], initialControls?: ControlPreferences): Promise<ManagedCredential[]> {
    return request(`${path()}/${connectionId}/credentials`, { method: "POST", body: JSON.stringify({ project_id: projectId, mappings, initial_controls: initialControls }) })
  },
  async beginHosting(platform: HostingPlatform, displayName: string, token?: string, connection?: Connection): Promise<{ authorization_url?: string; connection_id?: string }> {
    return request(path(), { method: "POST", body: JSON.stringify({ platform, display_name: displayName, token, connection_id: connection?.id, expected_revision: connection?.revision }) })
  },
  async getHostProjects(connectionId: string): Promise<HostProjects> {
    return request(`${path()}/${connectionId}`)
  },
  async saveHostProjects(connectionId: string, projectIds: string[], expectedRevision?: number): Promise<Connection> {
    return request(`${path()}/${connectionId}`, { method: "PUT", body: JSON.stringify({ project_ids: projectIds, expected_revision: expectedRevision ?? null }) })
  },
  async getHostVariables(connectionId: string, projectId: string): Promise<HostVariable[]> {
    return request(`${path()}/${connectionId}/variables?project_id=${encodeURIComponent(projectId)}`)
  },
}
