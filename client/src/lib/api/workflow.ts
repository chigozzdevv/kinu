import type { Approval, ApprovalEvidenceSnapshot, AuditEvent, Incident, Identifier, OverviewSummary, RotationHistory, RotationRun } from "../../types"
import type { StartRotationInput } from "./types"
import { nativeRequest } from "../native"
import { request, requestBlob, workspacePath, activeOrganisationId } from "./http"

export const workflow = {
  async getOverview(): Promise<OverviewSummary> {
    return request(`${workspacePath()}/overview`)
  },

  async getIncidents(): Promise<Incident[]> {
    return request(`${workspacePath()}/incidents`)
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

  async startRotation(input: StartRotationInput): Promise<RotationRun> {
    const response = await nativeRequest<{ run: RotationRun }>(activeOrganisationId(), "actions/rotation", {
      credentialId: input.credential_id, controlsId: input.control_version,
      requestId: crypto.randomUUID(), observedAt: new Date().toISOString(),
      reason: input.reason, urgency: input.urgency,
    })
    return response.run
  },

  async getDemo(): Promise<{ configured: boolean }> {
    return request(`${workspacePath()}/demo`)
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

