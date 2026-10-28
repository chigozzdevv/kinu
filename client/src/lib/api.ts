import { inventory } from "./api/inventory"
import { connections } from "./api/connections"
import { playbooks } from "./api/playbooks"
import { workflow } from "./api/workflow"
import { settings } from "./api/settings"

export const api = { ...inventory, ...connections, ...playbooks, ...workflow, ...settings }

export { ApiError, activeOrganisationId, setActiveOrganisationId } from "./api/http"
export type * from "./api/types"

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
} from "../types"
