import { z } from "zod"
import type { AgentEngine } from "@agent-native/core/agent/engine"
import { access, connection } from "./grant"
import { builderEngine } from "./engine"
import { withStorage } from "./storage"
import { reserve } from "./usage"

export const planStages = ["create", "store", "deploy", "verify", "rollout", "observe", "approval", "revoke", "complete"] as const
export const planInput = z.strictObject({
  trigger: z.enum(["exposure", "expiry", "drift", "manual", "schedule", "other"]),
  method: z.enum(["api", "browser"]),
  app_count: z.number().int().min(1).max(10000),
  approval_required: z.boolean(),
  observation_seconds: z.number().int().min(60).max(604800),
})
const planOutput = z.strictObject({
  strategy: z.literal("parallel"),
  stages: z.array(z.enum(planStages)).length(planStages.length),
  rationale: z.string().trim().min(1).max(1024),
})
export type PlanInput = z.infer<typeof planInput>

export async function propose(input: PlanInput, engine: AgentEngine, model: string, signal?: AbortSignal) {
  let text = "", complete = false
  for await (const event of engine.stream({ model, tools: [], maxOutputTokens: 1024,
    abortSignal: signal ? AbortSignal.any([signal, AbortSignal.timeout(45000)]) : AbortSignal.timeout(45000),
    systemPrompt: "Propose a credential rotation plan from the supplied facts. Return only JSON with strategy 'parallel', stages exactly " + JSON.stringify(planStages) + ", and a short rationale explaining the supplied facts and keeping the old key until verification. Mention approval only when approval_required is true. Never invent facts, keys, provider operations, application names, or approvals. The stages are mandatory and tools cannot be called.",
    messages: [{ role: "user", content: [{ type: "text", text: JSON.stringify(input) }] }],
  })) {
    if (event.type === "text-delta") text += event.text
    if (text.length > 8192 || event.type === "tool-call") throw new Error("Plan response exceeded its boundary")
    if (event.type === "stop") {
      if (!["end_turn", "stop_sequence"].includes(event.reason)) throw new Error("Plan response did not complete")
      complete = true
    }
  }
  if (!complete) throw new Error("Plan response was incomplete")
  const result = planOutput.parse(JSON.parse(text.trim().replace(/^```(?:json)?\s*([\s\S]*?)\s*```$/i, "$1")))
  if (result.stages.some((stage, index) => stage !== planStages[index])) throw new Error("Plan does not match required rotation stages")
  return result
}

export async function rotationPlanResponse(org: string, input: PlanInput, signal: AbortSignal) {
  try {
    return await withStorage(async (db) => {
      const current = await connection(db, org)
      if (!current || current.reconnect) return Response.json({ error: "Connect Builder AI in Settings before rotating." }, { status: 503 })
      await reserve(db, org)
      const result = await propose(input, builderEngine(await access(db, org)), current.model, signal)
      return Response.json(result, { headers: { "cache-control": "no-store" } })
    })
  } catch {
    return Response.json({ error: "Builder AI could not produce a rotation plan." }, { status: 502, headers: { "cache-control": "no-store" } })
  }
}
