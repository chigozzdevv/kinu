import type { Approval, ManagedCredential, RotationHistory, RotationRun, StageName } from "../types"

export function planStepState(history: RotationHistory | undefined, run: RotationRun, stage: StageName): "done" | "current" | "stopped" | "pending" {
  const activity = history?.stages.filter((item) => item.stage === stage).at(-1)
  if (activity?.status === "succeeded") return "done"
  if (activity?.status === "failed" || (run.stage === stage && ["failed", "cleanup-required"].includes(run.status))) return "stopped"
  if (run.stage === stage && ["pending", "running", "recovering", "paused"].includes(run.status)) return "current"
  return "pending"
}

const priority: Partial<Record<RotationRun["status"], number>> = {
  "cleanup-required": 5, recovering: 4, running: 3, paused: 2, pending: 1,
}

export function isBlockingRotation(run: RotationRun) {
  return Boolean(priority[run.status])
}

export function currentRotations(runs: readonly RotationRun[]): RotationRun[] {
  const sorted = [...runs].sort((left, right) =>
    (priority[right.status] ?? 0) - (priority[left.status] ?? 0)
    || Date.parse(right.created_at) - Date.parse(left.created_at)
    || right.id.localeCompare(left.id))
  const byCredential = new Map<string, RotationRun>()
  for (const run of sorted) {
    const key = `${run.organisation_id}:${run.credential_id}`
    if (!byCredential.has(key)) byCredential.set(key, run)
  }
  return [...byCredential.values()]
}

export function currentRotation(runs: readonly RotationRun[], credential: ManagedCredential) {
  return currentRotations(runs.filter((run) => run.organisation_id === credential.organisation_id && run.credential_id === credential.id))[0]
}

export function isActionableApproval(approval: Approval, run: RotationRun | undefined, credential: ManagedCredential | undefined, now = Date.now()) {
  return approval.decision === "pending" && !approval.consumed_at
    && Date.parse(approval.expires_at) > now
    && Boolean(run && credential && !credential.archived_at
      && credential.id === run.credential_id && credential.organisation_id === run.organisation_id
      && credential.control_version === run.control_version
      && run.id === approval.run_id && run.organisation_id === approval.organisation_id
      && ["running", "paused", "recovering"].includes(run.status)
      && run.plan_hash === approval.plan_hash
      && [run.current_generation_id, run.target_generation_id].includes(approval.generation_id))
}
