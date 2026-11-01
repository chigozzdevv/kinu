import { MongoServerError, type Db } from "mongodb"

const defaultLimit = 100
const scope = (organisation_id: string) => ({ organisation_id, day: new Date().toISOString().slice(0, 10) })

export class DailyLimitError extends Error {
  readonly code = "ai-daily-limit"
  constructor(limit: number) { super(`Kinu reached this workspace's daily limit of ${limit} AI calls`) }
}

export async function reserve(db: Db, organisationId: string) {
  const usage = db.collection("usage"), currentScope = scope(organisationId)
  try {
    await usage.updateOne(currentScope, { $setOnInsert: { calls: 0, limit: defaultLimit } }, { upsert: true })
  } catch (error) {
    if (!(error instanceof MongoServerError && error.code === 11000)) throw error
  }
  const result = await usage.updateOne({ ...currentScope, $expr: { $lt: ["$calls", { $ifNull: ["$limit", defaultLimit] }] } }, { $inc: { calls: 1 } })
  if (!result.modifiedCount) {
    const current = await usage.findOne(currentScope, { projection: { limit: 1 } })
    throw new DailyLimitError(typeof current?.limit === "number" ? current.limit : defaultLimit)
  }
}

export async function dailyUsage(db: Db, organisationId: string) {
  const current = await db.collection("usage").findOne(scope(organisationId), { projection: { calls: 1, limit: 1 } })
  return { calls: typeof current?.calls === "number" ? current.calls : 0,
    limit: typeof current?.limit === "number" ? current.limit : defaultLimit }
}
