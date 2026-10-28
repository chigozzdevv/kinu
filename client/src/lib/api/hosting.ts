import type { Connection } from "../../types"
import { request, workspacePath } from "./http"

export type HostingPlatform = "vercel" | "railway" | "render"
export interface HostProject { id: string; name: string }
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
  async beginHosting(platform: HostingPlatform, displayName: string, token?: string): Promise<{ authorization_url?: string; connection_id?: string }> {
    return request(path(), { method: "POST", body: JSON.stringify({ platform, display_name: displayName, token }) })
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
