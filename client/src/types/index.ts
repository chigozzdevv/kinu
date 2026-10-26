export type Identifier = string

export type MemberRole = "viewer" | "operator" | "administrator"
export type MemberStatus = "pending" | "active" | "disabled"

export interface Organisation {
  id: Identifier
  name: string
  created_at: string
  updated_at: string
  revision: number
}

export interface OrganisationMembership {
  organisation: Organisation
  role: MemberRole
}

export interface AccountSession {
  organisations: OrganisationMembership[]
}

export interface AccountProfile {
  id: Identifier
  organisation_id: Identifier
  display_name: string
  email: string
  connected_via: string
  role: MemberRole
  revision: number
}

export interface TeamMember {
  id: Identifier
  organisation_id: Identifier
  display_name: string | null
  email: string
  connected_via: string | null
  role: MemberRole
  status: MemberStatus
  created_at: string
  updated_at: string
  revision: number
}

export type NotificationKind =
  | "incident"
  | "incident-confirmation"
  | "rotation-due"
  | "rotation-failed"
  | "recovery-started"
  | "approval-required"
  | "old-key-used"
  | "connection-unhealthy"
  | "playbook-review"
  | "revocation-succeeded"
  | "rotation-completed"
  | "cleanup-required"

export interface EmailNotificationEndpoint {
  id: Identifier
  organisation_id: Identifier
  email_address: string
  event_kinds: NotificationKind[]
  enabled: boolean
  created_at: string
  updated_at: string
  revision: number
}

export interface NotificationTopic {
  id: Identifier
  label: string
  event_kinds: NotificationKind[]
}

export type ConnectionRole = "provider" | "secret-store" | "runtime" | "telemetry" | "incident"
export type ConnectionInterface = "api" | "browser"
export type ConnectionAuthorization = "oauth" | "workload-identity" | "api-key" | "browser-session"
export type ConnectionStatus = "setup-required" | "ready" | "reauthentication-required" | "degraded" | "disabled"

export interface HttpOperation {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  path: string
  success_statuses: number[]
  query: Record<string, string>
  body: Record<string, unknown>
  list_items: string | null
  provider_id_field: string | null
  secret_field: string | null
  name_field: string | null
  metadata_fields?: Record<string, string>
}

export interface HttpProviderApi {
  base_url: string
  auth: {
    scheme: "bearer" | "header" | "basic"
    header: string
    prefix: string | null
  }
  list_credentials: HttpOperation
  create_credential: HttpOperation
  revoke_credential: HttpOperation
  test_credential: HttpOperation | null
  credential_auth: {
    scheme: "bearer" | "header" | "basic"
    header: string
    prefix: string | null
  } | null
}

export interface Connection {
  id: Identifier
  organisation_id: Identifier
  platform: string
  display_name: string
  roles: ConnectionRole[]
  interface: ConnectionInterface
  authorization: ConnectionAuthorization
  authorization_reference: string | null
  capabilities: string[]
  allowed_resources: string[]
  http: HttpProviderApi | null
  playbook_id: Identifier | null
  playbook_version_id: Identifier | null
  status: ConnectionStatus
  authenticated_at: string | null
  authorization_expires_at: string | null
  last_validated_at: string | null
  region: string
  created_at: string
  updated_at: string
  archived_at?: string | null
  revision: number
}

export interface ProviderCredentialMetadata {
  provider_id: string
  name: string | null
  kind: string | null
  scopes: string[]
  status: string | null
  disabled: boolean | null
  created_at: string | null
  last_used_at: string | null
  expires_at: string | null
}

export interface RuntimeResourceMetadata {
  reference: string
  display_name: string
  endpoint: string | null
  identity: string | null
  region: string | null
  environment_name: string | null
  production: boolean | null
  secret_bindings: Array<{
    name: string
    secret: string
    version: string
    container: string | null
  }>
}

export interface Environment {
  id: Identifier
  organisation_id: Identifier
  application_id: Identifier
  display_name: string
  production: boolean
  region: string
  created_at: string
  updated_at: string
  archived_at?: string | null
  revision: number
}

export interface ConsumerService {
  id: Identifier
  organisation_id: Identifier
  application_id: Identifier
  environment_id: Identifier
  runtime_connection_id: Identifier
  telemetry_connection_ids: Identifier[]
  runtime_resource: string
  display_name: string
  endpoint: string | null
  repository: string | null
  identity: string | null
  created_at: string
  updated_at: string
  archived_at?: string | null
  revision: number
}

export interface ManagedCredential {
  id: Identifier
  organisation_id: Identifier
  connection_id: Identifier
  secret_store_connection_id: Identifier
  secret_resource: string
  secret_reference: string
  provider: string
  kind: string
  display_name: string
  provider_id: string | null
  scopes: string[]
  consumer_ids: Identifier[]
  active_generation_id: Identifier | null
  control_version: Identifier
  created_at: string
  updated_at: string
  archived_at?: string | null
  revision: number
}

export type GenerationState = "creating" | "active" | "superseded" | "revoked" | "orphaned" | "unknown"

export interface CredentialGeneration {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  provider_id: string | null
  fingerprint: string | null
  scopes: string[]
  state: GenerationState
  attempt_id: Identifier
  secret_reference: string | null
  predecessor_id: Identifier | null
  successor_id: Identifier | null
  created_at: string
  revoked_at: string | null
}

export interface ConsumerBinding {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  service_id: Identifier
  environment_id: Identifier
  runtime_connection_id: Identifier
  runtime_resource: string
  runtime_secret_name: string
  runtime_container_name: string | null
  secret_reference: string
  current_generation_id: Identifier
  target_generation_id: Identifier | null
  verification_report_id: Identifier | null
  required: boolean
  revision: number
}

export interface InventoryGraph {
  credentials: ManagedCredential[]
  services: ConsumerService[]
  bindings: ConsumerBinding[]
}

export interface Playbook {
  id: Identifier
  organisation_id: Identifier
  name: string
  platform: string
  latest_version: number
  latest_version_id: Identifier | null
  active_version_id: Identifier | null
  created_at: string
  updated_at: string
  archived_at?: string | null
  revision: number
}

export interface Incident {
  id: Identifier
  organisation_id: Identifier
  event_id: Identifier
  source: string
  kind: string
  source_event_id: string
  severity: "critical" | "high" | "medium" | "low"
  confidence: "verified" | "high" | "medium" | "low"
  status: "new" | "correlating" | "action-required" | "rotation-started" | "contained" | "resolved" | "dismissed"
  resource: {
    credential_id: Identifier | null
    repository: string | null
    project: string | null
    service: string | null
    environment: string | null
    provider: string | null
    provider_id: string | null
  }
  candidates: Array<{
    credential_id: Identifier
    confidence: "verified" | "high" | "medium" | "low"
    reasons: string[]
    consumer_ids: Identifier[]
  }>
  credential_id: Identifier | null
  run_id: Identifier | null
  dismissal_reason: string | null
  created_at: string
  updated_at: string
  revision: number
}

export type StageName =
  | "trigger"
  | "preflight"
  | "plan"
  | "create"
  | "store"
  | "deploy"
  | "verify"
  | "rollout"
  | "observe"
  | "approval"
  | "revoke"
  | "complete"

export type RunStatus = "pending" | "running" | "paused" | "recovering" | "cleanup-required" | "failed" | "cancelled" | "compensated" | "completed"

export interface Trigger {
  source: string
  kind: string
  event_id: string
  actor_id: Identifier
  reason: string
  urgency: string
  received_at: string
}

export interface RotationRun {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  trigger: Trigger
  control_version: Identifier
  stage: StageName
  status: RunStatus
  lease: { owner_id: Identifier; fencing_token: number; expires_at: string } | null
  fencing_token: number
  browser_playbook_version: Identifier | null
  plan_id: Identifier | null
  plan_hash: string | null
  current_generation_id: Identifier | null
  target_generation_id: Identifier | null
  failure: { code: string; message: string; retryable: boolean; evidence_ids: Identifier[] } | null
  recovery_id: Identifier | null
  recovery_stage: StageName | null
  recovery_mode: string | null
  recovery_failure: { code: string; message: string; retryable: boolean; evidence_ids: Identifier[] } | null
  recovery_evidence_ids: Identifier[]
  created_at: string
  updated_at: string
  revision: number
}

export type StageExecutionStatus = "succeeded" | "paused" | "failed" | "recovered"

export interface AgentDecisionSummary {
  agent: "inventory" | "planner" | "playbook" | "operator"
  decision: string
  explanation: string
}

export interface BrowserActionSummary {
  step_id: Identifier
  objective: string
  operation: string
  outcome: string
}

export interface StageDetail {
  label: string
  value: string
}

export interface RunStageActivity {
  id: Identifier
  stage: StageName
  status: StageExecutionStatus
  checks: string[]
  evidence_count: number
  summary: string | null
  details: StageDetail[]
  agent_decisions: AgentDecisionSummary[]
  browser_actions: BrowserActionSummary[]
  reason: string | null
  retryable: boolean
  started_at: string
  completed_at: string
}

export interface ComputerUseActivity {
  id: Identifier
  organisation_id: Identifier
  session_id: Identifier
  run_id: Identifier
  step_id: Identifier
  stage: StageName
  turn: number
  phase: "input" | "thought" | "response" | "proposal" | "validation" | "execution"
  status: "sent" | "streaming" | "proposed" | "validated" | "succeeded" | "paused" | "failed" | "completed"
  effect: "none" | "create-credential" | "revoke-credential" | null
  prompt: string | null
  instruction: string | null
  image_reference: string | null
  image_digest: string | null
  content: string | null
  action: "navigate" | "click" | "type" | "select" | "scroll" | "key" | "wait" | null
  arguments: Record<string, unknown>
  intent: string | null
  safety_decision: string | null
  target: string | null
  recorded_at: string
}

export interface RotationHistory {
  run_id: Identifier
  stages: RunStageActivity[]
  computer_use: ComputerUseActivity[]
}

export interface Approval {
  id: Identifier
  organisation_id: Identifier
  run_id: Identifier
  action_id: Identifier
  action_digest: string
  plan_hash: string
  evidence_hash: string
  generation_id: Identifier
  requested_by: Identifier
  capability_hash: string
  decision: "pending" | "approved" | "rejected" | "cancelled" | "more-evidence" | "extend-observation"
  approver_id: Identifier | null
  expires_at: string
  created_at: string
  decided_at: string | null
  consumed_at: string | null
  revision: number
}

export interface ApprovalEvidenceSnapshot {
  approval_id: Identifier
  evidence_hash: string
  kind: "verification" | "plan"
  status: string
  checks: string[]
  evidence_count: number
  recorded_at: string
}

export interface AuditEvent {
  id: Identifier
  organisation_id: Identifier
  sequence: number
  kind: string
  actor_id: Identifier
  resource: string
  run_id: Identifier | null
  payload: Record<string, string | number | boolean | null>
  evidence_ids: Identifier[]
  previous_hash: string
  event_hash: string
  occurred_at: string
  region: string
}

export interface OverviewSummary {
  credentials: number
  rotations_in_progress: number
  failed_rotations: number
  open_incidents: number
  pending_approvals: number
}
