import { createBuilderEngine } from "@agent-native/core/agent/engine"
import { runWithRequestContext } from "@agent-native/core/server/request-context"
import type { z } from "zod"
import { assess, inputSchema } from "./decision"
import { connection } from "./grant"
import { withStorage } from "./storage"

export async function assessment(orgId: string, input: z.infer<typeof inputSchema>) {
  return withStorage(async (db) => {
    const current = await connection(db, orgId)
    if (!current) throw new Error("Connect Builder.io for this workspace first")
    const allowance = await db.execute({
      sql: "INSERT INTO builder_usage VALUES (?, ?, 1) ON CONFLICT (organisation_id,day) DO UPDATE SET calls = builder_usage.calls + 1 WHERE builder_usage.calls < 20 RETURNING calls",
      args: [orgId, new Date().toISOString().slice(0, 10)],
    })
    if (!allowance.rows.length) throw new Error("The workspace reached its daily limit of 20 AI calls")
    const result = await runWithRequestContext({ userEmail: current.owner_email, orgId, credentialScope: "org" }, () =>
      assess(input, createBuilderEngine({ credentials: { privateKey: null, publicKey: null } }), current.model))
    await db.execute({ sql: "UPDATE builder_connections SET verified_at = ? WHERE organisation_id = ?", args: [Date.now(), orgId] })
    return result
  })
}
