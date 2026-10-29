import { MongoServerError, type Db } from "mongodb"

export class DailyLimitError extends Error {
  readonly code = "ai-daily-limit"
  constructor(limit: number) { super(`Kinu reached this workspace's daily limit of ${limit} AI calls`) }
}

export async function reserve(db: Db, organisationId: string) {
  const usage = db.collection("usage"), scope = { organisation_id: organisationId, day: new Date().toISOString().slice(0, 10) }
  try {
    await usage.updateOne(scope, { $setOnInsert: { calls: 0, limit: 20 } }, { upsert: true })
  } catch (error) {
    if (!(error instanceof MongoServerError && error.code === 11000)) throw error
  }
  const result = await usage.updateOne({ ...scope, $expr: { $lt: ["$calls", { $ifNull: ["$limit", 20] }] } }, { $inc: { calls: 1 } })
  if (!result.modifiedCount) {
    const current = await usage.findOne(scope, { projection: { limit: 1 } })
    throw new DailyLimitError(typeof current?.limit === "number" ? current.limit : 20)
  }
}
