import { MongoServerError, type Db } from "mongodb"

export async function reserve(db: Db, organisationId: string) {
  try {
    await db.collection("usage").updateOne({ organisation_id: organisationId,
      day: new Date().toISOString().slice(0, 10), calls: { $lt: 20 } },
    { $inc: { calls: 1 } }, { upsert: true })
  } catch (error) {
    if (error instanceof MongoServerError && error.code === 11000) throw new Error("The workspace reached its daily limit of 20 AI calls")
    throw error
  }
}
