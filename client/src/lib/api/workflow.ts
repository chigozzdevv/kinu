import type { Approval, ApprovalEvidenceSnapshot, AuditEvent, Incident, Identifier, OverviewSummary, RotationHistory, RotationRun } from "../../types"
import type { StartRotationInput } from "./types"
import { nativeRequest } from "../native"
import { request, requestBlob, workspacePath, activeOrganisationId } from "./http"

export type IncidentKind = "exposure" | "expiry" | "drift"
export interface IncidentReportOptions {
  email_sender: string
  recipient: string | null
  sources: Array<{ id: string; name: string; address: string; linked: boolean }>
  subject: string
  preview: string
}
export interface IncidentReportInput {
  credential_id: string
  kind: IncidentKind
  details: string
  simulate: boolean
  source_connection_id: string | null
  recipient: string | null
}
export interface IncidentReportStatus {
  id: string
  simulate: boolean
  state: string
  recipient: string | null
  message: string | null
  incident: Incident | null
}

export const workflow = {
  getIncidentReportOptions(credentialId?: string, kind: IncidentKind = "exposure"): Promise<IncidentReportOptions> {
    const parameters = new URLSearchParams({ kind })
    if (credentialId) parameters.set("credential_id", credentialId)
    return request(`${workspacePath()}/incidents/report-options?${parameters}`)
  },
  createIncidentReport(input: IncidentReportInput, requestId: string): Promise<IncidentReportStatus> {
    return request(`${workspacePath()}/incidents/reports`, {
      method: "POST", headers: { "Idempotency-Key": requestId }, body: JSON.stringify(input),
    })
  },
  getIncidentReportStatus(reportId: string): Promise<IncidentReportStatus> {
    return request(`${workspacePath()}/incidents/reports/${encodeURIComponent(reportId)}`)
  },

  async getOverview(): Promise<OverviewSummary> {
    return request(`${workspacePath()}/overview`)
  },

  async getIncidents(status?: Incident["status"]): Promise<Incident[]> {
    return request(`${workspacePath()}/incidents${status ? `?status=${encodeURIComponent(status)}` : ""}`)
  },

  async getRotationIncident(runId: string, eventId: string): Promise<Incident | null> {
    const path = workspacePath()
    let before = ""
    while (true) {
      const page = await request<Incident[]>(`${path}/incidents?limit=500${before ? `&before=${encodeURIComponent(before)}` : ""}`)
      const match = page.find((item) => item.run_id === runId || item.resolution_run_id === runId || item.event_id === eventId)
      if (match) return match
      if (page.length < 500) return null
      before = page[page.length - 1]!.id
    }
  },

  async getIncident(incidentId: string): Promise<Incident> {
    return request(`${workspacePath()}/incidents/${encodeURIComponent(incidentId)}`)
  },

  async getOpenIncidents(): Promise<Incident[]> {
    const path = workspacePath()
    const result: Incident[] = []
    let before = ""
    while (true) {
      const filters = "status=new&status=correlating&status=action-required&status=rotation-started&status=contained"
      const page = await request<Incident[]>(`${path}/incidents?${filters}&limit=500${before ? `&before=${encodeURIComponent(before)}` : ""}`)
      result.push(...page)
      if (page.length < 500) return result
      before = page[page.length - 1]!.id
    }
  },

  async confirmIncident(incidentId: Identifier, expectedRevision: number, credentialId: Identifier): Promise<Incident> {
    return request(`${workspacePath()}/incidents/${incidentId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, credential_id: credentialId }),
    })
  },

  async startIncidentRotation(incidentId: Identifier, controlVersion: Identifier, reason: string, urgency: "routine" | "urgent" | "emergency"): Promise<{ incident: Incident; run: RotationRun; applied: boolean }> {
    return request(`${workspacePath()}/incidents/${incidentId}/rotate`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ control_version: controlVersion, reason, urgency, received_at: new Date().toISOString() }),
    })
  },

  async dismissIncident(incidentId: Identifier, expectedRevision: number, reason: string): Promise<Incident> {
    return request(`${workspacePath()}/incidents/${incidentId}/dismiss`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, reason }),
    })
  },

  async getRotations(before?: string): Promise<RotationRun[]> {
    return request(`${workspacePath()}/runs${before ? `?before=${encodeURIComponent(before)}` : ""}`)
  },

  async getCurrentRotations(): Promise<RotationRun[]> {
    const path = workspacePath()
    const result: RotationRun[] = []
    let before = ""
    while (true) {
      const page = await request<RotationRun[]>(`${path}/runs?current=true&limit=500${before ? `&before=${encodeURIComponent(before)}` : ""}`)
      result.push(...page)
      if (page.length < 500) return result
      before = page[page.length - 1]!.id
    }
  },

  async startRotation(input: StartRotationInput): Promise<RotationRun> {
    const response = await nativeRequest<{ run: RotationRun }>(activeOrganisationId(), "actions/rotation", {
      credentialId: input.credential_id, controlsId: input.control_version,
      requestId: crypto.randomUUID(), observedAt: new Date().toISOString(),
      reason: input.reason, urgency: input.urgency,
    })
    return response.run
  },

  async getRotation(runId: string): Promise<RotationRun> {
    return request(`${workspacePath()}/runs/${runId}`)
  },

  async recoverRotation(runId: string, expectedRevision: number): Promise<RotationRun> {
    return request(`${workspacePath()}/runs/${runId}/recover`, {
      method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    })
  },

  async getRotationHistory(runId: string): Promise<RotationHistory> {
    return request(`${workspacePath()}/runs/${runId}/history`)
  },

  async getComputerUseImage(runId: string, activityId: string): Promise<Blob> {
    return requestBlob(`${workspacePath()}/runs/${runId}/computer-use/${activityId}/image`)
  },

  async getApprovals(): Promise<Approval[]> {
    return request(`${workspacePath()}/approvals`)
  },

  async getApprovalEvidence(approvalId: Identifier): Promise<ApprovalEvidenceSnapshot> {
    return request(`${workspacePath()}/approvals/${approvalId}/evidence`)
  },

  async decideApproval(approvalId: string, expectedRevision: number, decision: "approved" | "rejected" | "more-evidence" | "extend-observation"): Promise<Approval> {
    return request(`${workspacePath()}/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, decision }),
    })
  },

  async getAudits(): Promise<AuditEvent[]> {
    return request(`${workspacePath()}/audit`)
  }
}

