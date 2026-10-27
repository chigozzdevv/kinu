import { defineAction } from "@agent-native/core/action"
import { z } from "zod"
import { applicationRequest } from "../server/request"

export const schema = z.strictObject({ requestId: z.uuid() })

export default defineAction({
  description: "Queue a labelled demo email fixture for the workspace incident workflow.",
  schema,
  authorize: (_args, context) => Boolean(context?.orgId && context.requestHeaders),
  run: (input, context) => applicationRequest(context, "demo/alert", { method: "POST", idempotency: input.requestId, body: {} }),
})
