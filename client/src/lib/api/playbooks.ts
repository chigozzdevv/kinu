import type { Identifier, Playbook } from "../../types"
import type { PlaybookDefinition, PreparePlaybookInput, PreparedPlaybook, WalkthroughSource, PlaybookVersion, PlaybookDetail } from "./types"
import { request, archive, workspacePath } from "./http"

export const playbooks = {
  async preparePlaybook(input: PreparePlaybookInput): Promise<PreparedPlaybook> {
    const sourceIds: Identifier[] = []
    for (const item of input.sources) {
      const source = await request<WalkthroughSource>(`${workspacePath()}/playbooks/${input.playbook_id}/walkthroughs/references`, {
        method: "POST",
        body: JSON.stringify({ source_id: item.id, kind: item.kind,
          ...(item.kind === "text" ? { content: item.text } : { resource_url: item.resource }) }),
      })
      sourceIds.push(source.id)
    }
    const draft = await request<{ definition: PlaybookDefinition }>(`${workspacePath()}/playbooks/${input.playbook_id}/draft`, {
      method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ objective: input.objective, source_ids: sourceIds }),
    })
    return { playbook_id: input.playbook_id, version_id: input.version_id, source_ids: sourceIds, definition: draft.definition }
  },

  async savePlaybook(input: PreparedPlaybook): Promise<Playbook> {
    const created = await request<{ playbook: Playbook }>(`${workspacePath()}/playbooks/${input.playbook_id}/versions`, {
      method: "POST",
      body: JSON.stringify({ version_id: input.version_id, definition: input.definition, source_ids: input.source_ids }),
    })
    return created.playbook
  },

  async publishPlaybook(playbookId: Identifier, versionId: Identifier): Promise<PlaybookVersion> {
    return request(`${workspacePath()}/playbooks/${playbookId}/versions/${versionId}/publish`, { method: "POST", body: "{}" })
  },
  async getPlaybooks(): Promise<Playbook[]> {
    return request(`${workspacePath()}/playbooks`)
  },
  async getPlaybook(playbookId: Identifier): Promise<PlaybookDetail> {
    return request(`${workspacePath()}/playbooks/${playbookId}`)
  },
  async renamePlaybook(playbookId: Identifier, expectedRevision: number, name: string): Promise<Playbook> {
    return request(`${workspacePath()}/playbooks/${playbookId}`, {
      method: "PATCH", body: JSON.stringify({ expected_revision: expectedRevision, name }),
    })
  },
  async archivePlaybook(playbookId: Identifier, expectedRevision: number): Promise<Playbook> {
    return archive(`${workspacePath()}/playbooks/${playbookId}`, expectedRevision)
  },
}
