import type { Identifier, Playbook } from "../../types"
import type { PlaybookDefinition, PreparePlaybookInput, PreparedPlaybook, WalkthroughSource, BeginWalkthroughResponse, PlaybookVersion, PlaybookDetail } from "./types"
import { request, archive, workspacePath, ApiError } from "./http"

export const playbooks = {
  async importPlaybook(playbookId: Identifier, definition: PlaybookDefinition): Promise<{ playbook: Playbook; version: PlaybookVersion }> {
    return request(`${workspacePath()}/playbooks/${playbookId}/versions`, { method: "POST", body: JSON.stringify({ version_id: `version_${crypto.randomUUID().replaceAll("-", "")}`, definition, source_ids: [] }) })
  },
  async preparePlaybook(input: PreparePlaybookInput): Promise<PreparedPlaybook> {
    let source: WalkthroughSource
    if (input.source.kind === "text") {
      source = await request(`${workspacePath()}/playbooks/${input.playbook_id}/walkthroughs/references`, {
        method: "POST",
        body: JSON.stringify({ source_id: input.source.id, kind: "text", content: input.source.text }),
      })
    } else if (input.source.file) {
      const file = input.source.file
      const contentType = videoContentType(file)
      const started = await request<BeginWalkthroughResponse>(`${workspacePath()}/playbooks/${input.playbook_id}/walkthroughs`, {
        method: "POST",
        body: JSON.stringify({
          source_id: input.source.id,
          content_type: contentType,
          size: file.size,
          crc32c: await crc32c(file),
        }),
      })
      const uploaded = await fetch(started.upload_url, {
        method: "PUT",
        headers: {
          "Content-Type": contentType,
          "Content-Range": `bytes 0-${file.size - 1}/${file.size}`,
        },
        body: file,
      })
      if (!uploaded.ok) throw new ApiError(`Video upload failed (${uploaded.status})`, uploaded.status, "upload-failed")
      source = await request(`${workspacePath()}/playbooks/${input.playbook_id}/walkthroughs/${input.source.id}/complete`, {
        method: "POST",
        body: JSON.stringify({}),
      })
    } else {
      source = await request(`${workspacePath()}/playbooks/${input.playbook_id}/walkthroughs/video-references`, {
        method: "POST",
        body: JSON.stringify({ source_id: input.source.id, resource: input.source.resource }),
      })
    }
    source = await waitForWalkthrough(input.playbook_id, source)
    const draft = await request<{ definition: PlaybookDefinition }>(`${workspacePath()}/playbooks/${input.playbook_id}/draft`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ objective: input.objective, source_ids: [source.id] }),
    })
    return { playbook_id: input.playbook_id, version_id: input.version_id, source_id: source.id, definition: draft.definition }
  },

  async savePlaybook(input: PreparedPlaybook): Promise<Playbook> {
    const created = await request<{ playbook: Playbook }>(`${workspacePath()}/playbooks/${input.playbook_id}/versions`, {
      method: "POST",
      body: JSON.stringify({ version_id: input.version_id, definition: input.definition, source_ids: [input.source_id] }),
    })
    return created.playbook
  },

  async publishPlaybook(playbookId: Identifier, versionId: Identifier): Promise<PlaybookVersion> {
    return request(`${workspacePath()}/playbooks/${playbookId}/versions/${versionId}/publish`, {
      method: "POST",
      body: JSON.stringify({}),
    })
  },

  async getPlaybooks(): Promise<Playbook[]> {
    return request(`${workspacePath()}/playbooks`)
  },

  async getPlaybook(playbookId: Identifier): Promise<PlaybookDetail> {
    return request(`${workspacePath()}/playbooks/${playbookId}`)
  },

  async renamePlaybook(playbookId: Identifier, expectedRevision: number, name: string): Promise<Playbook> {
    return request(`${workspacePath()}/playbooks/${playbookId}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, name }),
    })
  },

  async archivePlaybook(playbookId: Identifier, expectedRevision: number): Promise<Playbook> {
    return archive(`${workspacePath()}/playbooks/${playbookId}`, expectedRevision)
  }
}


async function waitForWalkthrough(playbookId: Identifier, initial: WalkthroughSource): Promise<WalkthroughSource> {
    let source = initial
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (source.status === "ready") return source
      if (source.status === "failed") throw new ApiError(source.failure ?? "Video analysis failed", 422, "video-analysis-failed")
      if (attempt > 0) await wait(1500)
      source = await request(`${workspacePath()}/playbooks/${playbookId}/walkthroughs/${source.id}`)
    }
    throw new ApiError("Video analysis did not finish in time", 408, "video-analysis-timeout")
  }

function videoContentType(file: File) {
  const supported = new Set(["video/mp4", "video/webm", "video/quicktime"])
  if (supported.has(file.type)) return file.type
  const extension = file.name.toLowerCase().split(".").pop()
  const inferred = extension === "mp4" ? "video/mp4" : extension === "webm" ? "video/webm" : extension === "mov" ? "video/quicktime" : ""
  if (!inferred) throw new ApiError("Choose an MP4, WebM, or MOV video", 422, "video-type-invalid")
  return inferred
}

async function crc32c(file: File) {
  if (file.size <= 0 || file.size > 2_000_000_000) {
    throw new ApiError("Video size must be between 1 byte and 2 GB", 422, "video-size-invalid")
  }
  let checksum = 0xffffffff
  const reader = file.stream().getReader()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    for (const byte of value) {
      checksum ^= byte
      for (let bit = 0; bit < 8; bit += 1) {
        checksum = (checksum >>> 1) ^ ((checksum & 1) ? 0x82f63b78 : 0)
      }
    }
  }
  checksum = (checksum ^ 0xffffffff) >>> 0
  const bytes = new Uint8Array([
    (checksum >>> 24) & 0xff,
    (checksum >>> 16) & 0xff,
    (checksum >>> 8) & 0xff,
    checksum & 0xff,
  ])
  return btoa(String.fromCharCode(...bytes))
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}
