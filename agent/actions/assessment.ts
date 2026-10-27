import { defineAction } from "@agent-native/core/action"
import { assessment } from "../server/assessment"
import { inputSchema, resultSchema } from "../server/decision"

export { inputSchema, resultSchema }
export default defineAction({
  description: "Assess sanitized evidence using the workspace Builder connection.",
  schema: inputSchema, outputSchema: resultSchema, readOnly: true,
  authorize: (_args, context) => context?.caller === "automation" && Boolean(context.orgId) && context.appPermissions?.includes("agent.assess") === true,
  run: (input, context) => {
    if (!context?.orgId) throw new Error("A workspace is required for assessment")
    return assessment(context.orgId, input)
  },
})
