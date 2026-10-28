import type { Connection, CredentialGeneration, Identifier, ManagedCredential, Playbook } from "../../types"

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

export interface WalkthroughSource {
  id: Identifier
  status: "uploading" | "analysing" | "ready" | "failed"
  failure?: string | null
}

export interface BeginWalkthroughResponse {
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

export interface Features {
  hosting: boolean
  aws_connections: boolean
  github: boolean
  gmail: boolean
  provider_api: boolean
  browser_setup: boolean
  browser_execution: boolean
  playbook_generation: boolean
}
