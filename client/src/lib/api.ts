import { inventory } from "./api/inventory"
import { connections } from "./api/connections"
import { playbooks } from "./api/playbooks"
import { workflow } from "./api/workflow"
import { settings } from "./api/settings"
import { integrations } from "./api/integrations"
import { hosting } from "./api/hosting"
import { gmail } from "./api/gmail"

export const api = { ...inventory, ...connections, ...playbooks, ...workflow, ...settings, ...gmail, ...integrations, ...hosting }

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
