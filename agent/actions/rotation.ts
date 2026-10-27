import { defineAction, type ActionRunContext } from "@agent-native/core/action"
import { z } from "zod"
import { applicationRequest } from "../server/request"

const identifier = z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/)

export const schema = z.strictObject({
    credentialId: identifier,
    controlsId: identifier,
    requestId: z.uuid(),
    observedAt: z.iso.datetime({ offset: true }),
    reason: z.string().min(1).max(1024),
    urgency: z.enum(["routine", "urgent", "emergency"]),
})

export default defineAction({
  description: "Request a rotation under workspace access controls and verified execution stages.",
  schema,
  authorize: (_args, context) => Boolean(context?.orgId && context.requestHeaders),
  run: async (args, context?: ActionRunContext) => applicationRequest(context, "runs", {
    method: "POST",
    idempotency: args.requestId,
    body: {
      credential_id: args.credentialId, control_version: args.controlsId,
      source: "agent-native", kind: "manual", event_id: args.requestId,
      reason: args.reason, urgency: args.urgency, received_at: args.observedAt,
    },
  }),
})
