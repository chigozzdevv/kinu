import { defineAction, type ActionRunContext } from "@agent-native/core/action"
import { z } from "zod"
import { applicationRequest } from "../server/request"

export const schema = z.strictObject({ resource: z.enum(["inventory", "incidents", "runs"]) })

export default defineAction({
  description: "Read the selected workspace's credential relationships, incidents, or rotation runs.",
  schema,
  readOnly: true,
  authorize: (_args, context) => Boolean(context?.orgId && context.requestHeaders),
  run: async ({ resource }, context?: ActionRunContext) => {
    const path = resource === "inventory" ? "inventory/graph" : resource
    return applicationRequest(context, path)
  },
})
