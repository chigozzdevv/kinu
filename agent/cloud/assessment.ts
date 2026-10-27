import assessment, { inputSchema } from "../actions/assessment"
import { defineAppConfig } from "@agent-native/core/app-config"
import { z } from "zod"

defineAppConfig({ migration: { skipEnsureTables: true }, observability: { enabled: false, capturePrompts: false, captureToolArgs: false, captureToolResults: false } })
const schema = z.strictObject({ organisation_id: z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/), input: inputSchema })

export async function handler(event: unknown) {
  const request = schema.parse(event)
  return assessment.run(request.input, {
    caller: "automation", orgId: request.organisation_id, appPermissions: ["agent.assess"],
  })
}
