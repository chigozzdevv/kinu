import type { z } from "zod"
import { assess, inputSchema } from "./decision"
import { access, connection } from "./grant"
import { builderEngine } from "./engine"
import { withStorage } from "./storage"
import { reserve } from "./usage"

export async function assessment(orgId: string, input: z.infer<typeof inputSchema>) {
  return withStorage(async (db) => {
    const current = await connection(db, orgId)
    if (!current || current.reconnect) throw new Error("Connect Builder.io for this workspace first")
    await reserve(db, orgId)
    const result = await assess(input, builderEngine(await access(db, orgId)), current.model)
    await db.collection("connections").updateOne({ organisation_id: orgId, revision: current.revision },
      { $set: { verified_at: Date.now() } })
    return result
  })
}
