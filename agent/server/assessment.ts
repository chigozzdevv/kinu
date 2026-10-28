import { MongoServerError } from "mongodb"
import type { z } from "zod"
import { assess, inputSchema } from "./decision"
import { access, connection } from "./grant"
import { builderEngine } from "./engine"
import { withStorage } from "./storage"

export async function assessment(orgId: string, input: z.infer<typeof inputSchema>) {
  return withStorage(async (db) => {
    const current = await connection(db, orgId)
    if (!current || current.reconnect) throw new Error("Connect Builder.io for this workspace first")
    try {
      await db.collection("usage").updateOne({ organisation_id: orgId,
        day: new Date().toISOString().slice(0, 10), calls: { $lt: 20 } },
      { $inc: { calls: 1 } }, { upsert: true })
    } catch (error) {
      if (error instanceof MongoServerError && error.code === 11000) throw new Error("The workspace reached its daily limit of 20 AI calls")
      throw error
    }
    const result = await assess(input, builderEngine(await access(db, orgId)), current.model)
    await db.collection("connections").updateOne({ organisation_id: orgId, revision: current.revision },
      { $set: { verified_at: Date.now() } })
    return result
  })
}
