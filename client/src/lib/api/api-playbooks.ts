import type { HttpProviderApi, Playbook } from "../../types"
import type { PreparePlaybookInput, WalkthroughSource } from "./types"
import { request, workspacePath, archive } from "./http"

export interface ApiPlaybookDefinition {
  name: string
  platform: string
  http: HttpProviderApi
}
export interface PreparedApiPlaybook {
  playbook_id: string
  version_id: string
  source_ids: string[]
  definition: ApiPlaybookDefinition
}
export interface ApiPlaybookVersion {
  id: string
  number: number
  state: "draft" | "published" | "superseded" | "review-required"
  definition: ApiPlaybookDefinition
}
export interface ApiPlaybookDetail {
  playbook: Playbook
  active_version: ApiPlaybookVersion | null
  latest_version: ApiPlaybookVersion | null
}
const path = () => `${workspacePath()}/api-playbooks`

export const apiPlaybooks = {
  async prepareApiPlaybook(input: PreparePlaybookInput): Promise<PreparedApiPlaybook> {
    const sourceIds: string[] = []
    for (const item of input.sources) {
      const source = await request<WalkthroughSource>(`${workspacePath()}/playbooks/${input.playbook_id}/walkthroughs/references`, {
        method: "POST", body: JSON.stringify({ source_id: item.id, kind: item.kind,
          ...(item.kind === "text" ? { content: item.text } : { resource_url: item.resource }) }),
      })
      sourceIds.push(source.id)
    }
    const draft = await request<{ definition: ApiPlaybookDefinition }>(`${path()}/${input.playbook_id}/draft`, {
      method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ objective: input.objective, source_ids: sourceIds }),
    })
    return { playbook_id: input.playbook_id, version_id: input.version_id, source_ids: sourceIds, definition: draft.definition }
  },
  async saveApiPlaybook(input: PreparedApiPlaybook): Promise<Playbook> {
    return (await request<{ playbook: Playbook }>(`${path()}/${input.playbook_id}/versions`, {
      method: "POST", body: JSON.stringify({ version_id: input.version_id, definition: input.definition, source_ids: input.source_ids }),
    })).playbook
  },
  getApiPlaybooks: () => request<Playbook[]>(path()),
  getApiPlaybook: (id: string) => request<ApiPlaybookDetail>(`${path()}/${id}`),
  publishApiPlaybook: (id: string, versionId: string) => request<ApiPlaybookVersion>(`${path()}/${id}/versions/${versionId}/publish`, { method: "POST", body: "{}" }),
  archiveApiPlaybook: (id: string, revision: number) => archive<Playbook>(`${path()}/${id}`, revision),
}
