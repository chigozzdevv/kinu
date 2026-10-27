import type {
  AccountProfile,
  AccountSession,
  Approval,
  ApprovalEvidenceSnapshot,
  AuditEvent,
  Connection,
  CredentialGeneration,
  Environment,
  Incident,
  Identifier,
  InventoryGraph,
  ManagedCredential,
  EmailNotificationEndpoint,
  NotificationTopic,
  OrganisationMembership,
  OverviewSummary,
  Playbook,
  ProviderCredentialMetadata,
  RuntimeResourceMetadata,
  RotationHistory,
  RotationRun,
  TeamMember,
  MemberRole,
} from "../types";
import { clearSession, sessionHeaders } from "./auth"
import { nativeRequest } from "./native"

let organisationId = ""
let ROOT = ""

export function setActiveOrganisationId(value: Identifier) {
  if (!/^[a-z][a-z0-9_-]{2,127}$/.test(value)) throw new Error("Organisation ID is invalid")
  organisationId = value
  ROOT = `/v1/organisations/${value}`
}

export function activeOrganisationId(): Identifier {
  if (!organisationId) throw new Error("Select an organisation first")
  return organisationId
}

export interface ImportCredentialInput {
  credential: ManagedCredential
  generation: CredentialGeneration
  consumer: {
    application_id: Identifier
    environment_id: Identifier
    service_id: Identifier
    binding_id: Identifier
    runtime_connection_id: Identifier
    runtime_resource: string
    runtime_secret_name: string
    runtime_container_name?: string
    environment_name?: string
  }
  controls: ControlPreferences
}

export interface CreateConnectionInput {
  connection: Connection
  playbook_id?: Identifier
  playbook_version_id?: Identifier
}

export interface PlaybookDefinition {
  name: string
  platform: string
  allowed_domains: string[]
  login_url_pattern: string
  steps: Array<{
    id: Identifier
    stage: "create" | "revoke"
    effect: "none" | "create-credential" | "revoke-credential"
    tool: string
    operation: string
    objective: string
    parameters: Record<string, string | number | boolean | string[]>
    protected: boolean
    evidence_checks: string[]
    selectors: Array<{ kind: "role" | "label" | "text" | "test-id" | "css"; value: string; name: string | null; exact: boolean }>
    checkpoint: { url_pattern: string; required_text: string[]; forbidden_text: string[] }
    secure_field: { name: Identifier; selector: { kind: "role" | "label" | "text" | "test-id" | "css"; value: string; name: string | null; exact: boolean }; provider_id_selector: { kind: "role" | "label" | "text" | "test-id" | "css"; value: string; name: string | null; exact: boolean } } | null
    outputs: never[]
    timeout_seconds: number
    retry_limit: number
  }>
}

export interface PreparePlaybookInput {
  playbook_id: Identifier
  version_id: Identifier
  source: {
    id: Identifier
    kind: "text" | "video"
    text?: string
    file?: File
    resource?: string
  }
  objective: string
}

export interface PreparedPlaybook {
  playbook_id: Identifier
  version_id: Identifier
  source_id: Identifier
  definition: PlaybookDefinition
}

interface WalkthroughSource {
  id: Identifier
  status: "uploading" | "analysing" | "ready" | "failed"
  failure?: string | null
}

interface BeginWalkthroughResponse {
  source: WalkthroughSource
  upload_url: string
}

export interface ControlDefinition {
  required_checks: Record<string, string[]>
  allowed_tools: string[]
  protected_tools: string[]
  allowed_recovery_modes: Array<"retry" | "rollback" | "rollforward" | "cleanup" | "escalate">
  maximum_observation_seconds: number
  require_revoke_approval: boolean
  preserve_old_generation: boolean
  require_generation_telemetry: boolean
  rotate_before_expiry_seconds: number
  maximum_metadata_age_seconds: number
  require_runtime_alignment: boolean
  automatic_triggers: string[]
  emergency_triggers: string[]
  exposure_sources: ExposureSource[]
  minimum_automatic_confidence: "verified" | "high" | "medium" | "low"
  probe_versions: Record<string, string[]>
  recovery: Record<string, unknown>
}

export interface ControlPreferences {
  automatic_triggers: string[]
  rotate_before_expiry_seconds: number
  maximum_observation_seconds: number
  require_revoke_approval: boolean
  exposure_sources: ExposureSource[]
}

export interface ExposureSource {
  connection_id: Identifier
  resource: string
}

export interface SecretResourceMetadata {
  reference: string
  display_name: string
}

export interface SecretVersionMetadata {
  reference: string
  state: string
  created_at: string | null
}

export interface CreateNotificationEndpointInput {
  id: Identifier
  email_address: string
  topics: Identifier[]
}

export interface StartRotationInput {
  credential_id: Identifier
  control_version: Identifier
  reason: string
  urgency: "routine" | "urgent" | "emergency"
}

export interface BrowserSetupResponse {
  session: BrowserSetupSession
  token: string
  setup_url: string
  gateway_url: string
  expires_at: string
}

export interface BrowserSetupSession {
  id: Identifier
  revision: number
  expires_at: string
  status: "provisioning" | "ready" | "capturing" | "complete" | "terminated"
}

export interface GitHubOnboardingResponse {
  session: { id: Identifier; expires_at: string }
  state: string
  pkce_verifier: string
  installation_url: string
  authorization_url: string
}

export interface GitHubRepositoryCandidate {
  repository_id: number
  full_name: string
  private: boolean
  default_branch: string
  secret_scanning: "enabled" | "disabled" | "unavailable"
}

export interface GitHubDiscoveryResponse {
  session: { id: Identifier; status: "discovered" | "complete"; expires_at: string }
  installation: {
    installation_id: number
    account_login: string
    ready: boolean
  }
  repositories: GitHubRepositoryCandidate[]
}

export interface GitHubCompletionResponse extends GitHubDiscoveryResponse {
  session: { id: Identifier; status: "complete"; expires_at: string }
  repositories: GitHubRepositoryCandidate[]
}

export interface PlaybookVersion {
  id: Identifier
  playbook_id: Identifier
  number: number
  definition: PlaybookDefinition
  state: "draft" | "published" | "superseded" | "review-required"
  source_ids: Identifier[]
}

export interface ControlVersion {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  number: number
  definition: ControlDefinition
  digest: string
  created_by: Identifier
  created_at: string
}

export interface PlaybookDetail {
  playbook: Playbook
  active_version: PlaybookVersion | null
  latest_version: PlaybookVersion | null
}

export interface CredentialControlsResponse {
  credential: ManagedCredential
  controls: ControlVersion
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(
    message: string,
    status: number,
    code: string,
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

class ApiClient {
  private async authenticatedFetch(path: string, options?: RequestInit): Promise<Response> {
    const headers = new Headers(options?.headers)
    const authentication = await sessionHeaders(options?.method)
    authentication.forEach((value, name) => headers.set(name, value))
    if (options?.body) headers.set("Content-Type", "application/json")

    const response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      headers,
    })
    if (response.status === 401) {
      clearSession()
      window.location.assign("/auth")
    }
    return response
  }

  private async request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await this.authenticatedFetch(path, options)
    if (!response.ok) {
      const problem = (await response.json().catch(() => null)) as { code?: string; message?: string } | null
      throw new ApiError(
        problem?.message ?? `Kinu API request failed (${response.status})`,
        response.status,
        problem?.code ?? "request-failed",
      )
    }
    return response.json() as Promise<T>
  }

  private async requestBlob(path: string): Promise<Blob> {
    const response = await this.authenticatedFetch(path, {
      headers: { Accept: "image/png" },
    })
    if (!response.ok) {
      const problem = (await response.json().catch(() => null)) as { code?: string; message?: string } | null
      throw new ApiError(
        problem?.message ?? `Kinu API request failed (${response.status})`,
        response.status,
        problem?.code ?? "request-failed",
      )
    }
    return response.blob()
  }

  async getSession(): Promise<AccountSession> {
    return this.request("/v1/session")
  }

  async createOrganisation(name: string): Promise<OrganisationMembership> {
    return this.request("/v1/organisations", {
      method: "POST",
      body: JSON.stringify({ name }),
    })
  }

  async getOverview(): Promise<OverviewSummary> {
    return this.request(`${ROOT}/overview`)
  }

  async getGraph(): Promise<InventoryGraph> {
    return this.request(`${ROOT}/inventory/graph`)
  }

  async getCredential(credentialId: Identifier): Promise<ManagedCredential> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}`)
  }

  async updateCredential(credentialId: Identifier, input: { expected_revision: number; display_name?: string }): Promise<ManagedCredential> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  }

  async archiveCredential(credentialId: Identifier, expectedRevision: number): Promise<ManagedCredential> {
    return this.archive(`${ROOT}/inventory/credentials/${credentialId}`, expectedRevision)
  }

  async importCredential(input: ImportCredentialInput): Promise<ManagedCredential> {
    return this.request(`${ROOT}/inventory/credentials`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async getCredentialControls(credentialId: Identifier, versionId: Identifier): Promise<ControlVersion> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}/controls/${versionId}`)
  }

  async updateCredentialControls(
    credentialId: Identifier,
    input: { expected_revision: number; version_id: Identifier; controls: ControlPreferences },
  ): Promise<CredentialControlsResponse> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}/controls`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async createConnection(input: CreateConnectionInput): Promise<Connection> {
    const created = await this.request<Connection>(`${ROOT}/inventory/connections`, {
      method: "POST",
      body: JSON.stringify(input.connection),
    })
    if (!input.playbook_id || !input.playbook_version_id) return created
    return this.attachPlaybook(created, input.playbook_id, input.playbook_version_id)
  }

  async getConnection(connectionId: Identifier): Promise<Connection> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}`)
  }

  async resolveCredential(connectionId: Identifier, input: { secret_store_connection_id: Identifier; secret_reference: string }): Promise<ProviderCredentialMetadata> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/resolve-credential`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async getRuntimeResources(connectionId: Identifier): Promise<RuntimeResourceMetadata[]> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/runtime-resources`)
  }

  async getSecretResources(connectionId: Identifier): Promise<SecretResourceMetadata[]> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/secret-resources`)
  }

  async getSecretVersions(connectionId: Identifier, secret: string): Promise<SecretVersionMetadata[]> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/secret-versions?secret=${encodeURIComponent(secret)}`)
  }

  async updateConnection(connectionId: Identifier, input: { expected_revision: number; display_name?: string; capabilities?: string[]; allowed_resources?: string[]; region?: string }): Promise<Connection> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  }

  async archiveConnection(connectionId: Identifier, expectedRevision: number): Promise<Connection> {
    return this.archive(`${ROOT}/inventory/connections/${connectionId}`, expectedRevision)
  }

  async attachPlaybook(connection: Connection, playbookId: Identifier, versionId: Identifier): Promise<Connection> {
    return this.request(`${ROOT}/playbooks/${playbookId}/versions/${versionId}/attach`, {
      method: "POST",
      body: JSON.stringify({ connection_id: connection.id, expected_revision: connection.revision }),
    })
  }

  async beginBrowserSetup(connectionId: Identifier): Promise<BrowserSetupResponse> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/setup`, {
      method: "POST",
      body: JSON.stringify({ extra_domains: [] }),
    })
  }

  async getBrowserSetup(setupId: Identifier): Promise<BrowserSetupSession> {
    return this.request(`${ROOT}/inventory/setups/${setupId}`)
  }

  async beginGitHubOnboarding(): Promise<GitHubOnboardingResponse> {
    return this.request(`${ROOT}/github/onboarding`, { method: "POST", body: JSON.stringify({}) })
  }

  async discoverGitHubOnboarding(
    sessionId: Identifier,
    input: { state: string; pkce_verifier: string; code: string; installation_id?: number },
  ): Promise<GitHubDiscoveryResponse> {
    return this.request(`${ROOT}/github/onboarding/${sessionId}/discover`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async completeGitHubOnboarding(
    sessionId: Identifier,
  ): Promise<GitHubCompletionResponse> {
    return this.request(`${ROOT}/github/onboarding/${sessionId}/complete`, {
      method: "POST",
      body: JSON.stringify({}),
    })
  }

  async completeBrowserSetup(setupId: Identifier, expectedRevision: number, token: string): Promise<{ connection: Connection }> {
    return this.request(`${ROOT}/inventory/setups/${setupId}/complete`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, token }),
    })
  }

  async abortBrowserSetup(setupId: Identifier, expectedRevision: number): Promise<void> {
    await this.request(`${ROOT}/inventory/setups/${setupId}/abort`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    })
  }

  async preparePlaybook(input: PreparePlaybookInput): Promise<PreparedPlaybook> {
    let source: WalkthroughSource
    if (input.source.kind === "text") {
      source = await this.request(`${ROOT}/playbooks/${input.playbook_id}/walkthroughs/references`, {
        method: "POST",
        body: JSON.stringify({ source_id: input.source.id, kind: "text", content: input.source.text }),
      })
    } else if (input.source.file) {
      const file = input.source.file
      const contentType = videoContentType(file)
      const started = await this.request<BeginWalkthroughResponse>(`${ROOT}/playbooks/${input.playbook_id}/walkthroughs`, {
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
      source = await this.request(`${ROOT}/playbooks/${input.playbook_id}/walkthroughs/${input.source.id}/complete`, {
        method: "POST",
        body: JSON.stringify({}),
      })
    } else {
      source = await this.request(`${ROOT}/playbooks/${input.playbook_id}/walkthroughs/video-references`, {
        method: "POST",
        body: JSON.stringify({ source_id: input.source.id, resource: input.source.resource }),
      })
    }
    source = await this.waitForWalkthrough(input.playbook_id, source)
    const draft = await this.request<{ definition: PlaybookDefinition }>(`${ROOT}/playbooks/${input.playbook_id}/draft`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ objective: input.objective, source_ids: [source.id] }),
    })
    return { playbook_id: input.playbook_id, version_id: input.version_id, source_id: source.id, definition: draft.definition }
  }

  async savePlaybook(input: PreparedPlaybook): Promise<Playbook> {
    const created = await this.request<{ playbook: Playbook }>(`${ROOT}/playbooks/${input.playbook_id}/versions`, {
      method: "POST",
      body: JSON.stringify({ version_id: input.version_id, definition: input.definition, source_ids: [input.source_id] }),
    })
    return created.playbook
  }

  private async waitForWalkthrough(playbookId: Identifier, initial: WalkthroughSource): Promise<WalkthroughSource> {
    let source = initial
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (source.status === "ready") return source
      if (source.status === "failed") throw new ApiError(source.failure ?? "Video analysis failed", 422, "video-analysis-failed")
      if (attempt > 0) await wait(1500)
      source = await this.request(`${ROOT}/playbooks/${playbookId}/walkthroughs/${source.id}`)
    }
    throw new ApiError("Video analysis did not finish in time", 408, "video-analysis-timeout")
  }

  async publishPlaybook(playbookId: Identifier, versionId: Identifier): Promise<PlaybookVersion> {
    return this.request(`${ROOT}/playbooks/${playbookId}/versions/${versionId}/publish`, {
      method: "POST",
      body: JSON.stringify({}),
    })
  }

  async getEnvironments(): Promise<Environment[]> {
    return this.request(`${ROOT}/inventory/environments`)
  }

  async getIncidents(): Promise<Incident[]> {
    return this.request(`${ROOT}/incidents`)
  }

  async confirmIncident(incidentId: Identifier, expectedRevision: number, credentialId: Identifier): Promise<Incident> {
    return this.request(`${ROOT}/incidents/${incidentId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, credential_id: credentialId }),
    })
  }

  async startIncidentRotation(incidentId: Identifier, controlVersion: Identifier, reason: string, urgency: "routine" | "urgent" | "emergency"): Promise<{ incident: Incident; run: RotationRun; applied: boolean }> {
    return this.request(`${ROOT}/incidents/${incidentId}/rotate`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ control_version: controlVersion, reason, urgency, received_at: new Date().toISOString() }),
    })
  }

  async dismissIncident(incidentId: Identifier, expectedRevision: number, reason: string): Promise<Incident> {
    return this.request(`${ROOT}/incidents/${incidentId}/dismiss`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, reason }),
    })
  }

  async getRotations(): Promise<RotationRun[]> {
    return this.request(`${ROOT}/runs`)
  }

  async startRotation(input: StartRotationInput): Promise<RotationRun> {
    const response = await nativeRequest<{ run: RotationRun }>(activeOrganisationId(), "actions/rotation", {
      credentialId: input.credential_id, controlsId: input.control_version,
      requestId: crypto.randomUUID(), observedAt: new Date().toISOString(),
      reason: input.reason, urgency: input.urgency,
    })
    return response.run
  }

  async getDemo(): Promise<{ configured: boolean }> {
    return this.request(`${ROOT}/demo`)
  }

  async getRotation(runId: string): Promise<RotationRun> {
    return this.request(`${ROOT}/runs/${runId}`)
  }

  async getRotationHistory(runId: string): Promise<RotationHistory> {
    return this.request(`${ROOT}/runs/${runId}/history`)
  }

  async getComputerUseInputImage(runId: string, activityId: string): Promise<Blob> {
    return this.requestBlob(`${ROOT}/runs/${runId}/computer-use/${activityId}/image`)
  }

  async getApprovals(): Promise<Approval[]> {
    return this.request(`${ROOT}/approvals`)
  }

  async getApprovalEvidence(approvalId: Identifier): Promise<ApprovalEvidenceSnapshot> {
    return this.request(`${ROOT}/approvals/${approvalId}/evidence`)
  }

  async decideApproval(approvalId: string, expectedRevision: number, decision: "approved" | "rejected" | "more-evidence" | "extend-observation"): Promise<Approval> {
    return this.request(`${ROOT}/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, decision }),
    })
  }

  async getPlaybooks(): Promise<Playbook[]> {
    return this.request(`${ROOT}/playbooks`)
  }

  async getPlaybook(playbookId: Identifier): Promise<PlaybookDetail> {
    return this.request(`${ROOT}/playbooks/${playbookId}`)
  }

  async renamePlaybook(playbookId: Identifier, expectedRevision: number, name: string): Promise<Playbook> {
    return this.request(`${ROOT}/playbooks/${playbookId}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, name }),
    })
  }

  async archivePlaybook(playbookId: Identifier, expectedRevision: number): Promise<Playbook> {
    return this.archive(`${ROOT}/playbooks/${playbookId}`, expectedRevision)
  }

  async getConnections(): Promise<Connection[]> {
    return this.request(`${ROOT}/inventory/connections`)
  }

  async getAudits(): Promise<AuditEvent[]> {
    return this.request(`${ROOT}/audit`)
  }

  async getProfile(): Promise<AccountProfile> {
    return this.request(`${ROOT}/settings/profile`)
  }

  async updateProfile(expectedRevision: number, displayName: string): Promise<AccountProfile> {
    return this.request(`${ROOT}/settings/profile`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, display_name: displayName }),
    })
  }

  async getTeam(): Promise<TeamMember[]> {
    return this.request(`${ROOT}/settings/team`)
  }

  async inviteTeamMember(email: string, role: MemberRole): Promise<TeamMember> {
    return this.request(`${ROOT}/settings/team/invitations`, {
      method: "POST",
      body: JSON.stringify({ email, role }),
    })
  }

  async updateTeamMember(member: TeamMember, role: MemberRole, enabled: boolean): Promise<TeamMember> {
    return this.request(`${ROOT}/settings/team/members/${member.id}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: member.revision, role, enabled }),
    })
  }

  async cancelTeamInvitation(member: TeamMember): Promise<TeamMember> {
    return this.request(`${ROOT}/settings/team/invitations/${member.id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: member.revision }),
    })
  }

  async getNotificationTopics(): Promise<NotificationTopic[]> {
    return this.request(`${ROOT}/notifications/topics`)
  }

  async getNotificationEndpoints(): Promise<EmailNotificationEndpoint[]> {
    return this.request(`${ROOT}/notifications/endpoints`)
  }

  async createNotificationEndpoint(input: CreateNotificationEndpointInput): Promise<EmailNotificationEndpoint> {
    return this.request(`${ROOT}/notifications/endpoints`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async setNotificationEndpointEnabled(endpointId: Identifier, expectedRevision: number, enabled: boolean): Promise<EmailNotificationEndpoint> {
    return this.request(`${ROOT}/notifications/endpoints/${endpointId}/state`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, enabled }),
    })
  }

  private async archive<T>(resourcePath: string, expectedRevision: number): Promise<T> {
    return this.request(`${resourcePath}/archive`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, cascade: true }),
    })
  }
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

export const api = new ApiClient()

export type {
  Approval,
  AuditEvent,
  Connection,
  Environment,
  Incident,
  ManagedCredential,
  EmailNotificationEndpoint,
  OverviewSummary,
  Playbook,
  RotationRun,
}
