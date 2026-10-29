import assert from "node:assert/strict"
import test from "node:test"
import { MongoClient } from "mongodb"
import { MongoMemoryServer } from "mongodb-memory-server"
import { DailyLimitError, reserve } from "../server/usage"

test("daily allowance remains atomic and preserves calls when a scoped limit is increased", async () => {
  const server = await MongoMemoryServer.create(), client = new MongoClient(server.getUri())
  try {
    const db = client.db("usage_check"), day = new Date().toISOString().slice(0, 10)
    await db.collection("usage").createIndex({ organisation_id: 1, day: 1 }, { unique: true })
    const results = await Promise.allSettled(Array.from({ length: 25 }, () => reserve(db, "org_one")))
    assert.equal(results.filter(result => result.status === "fulfilled").length, 20)
    assert.equal((await db.collection("usage").findOne({ organisation_id: "org_one", day }))?.calls, 20)
    assert.ok(results.filter(result => result.status === "rejected").every(result => result.reason instanceof DailyLimitError))
    await db.collection("usage").updateOne({ organisation_id: "org_one", day }, { $set: { limit: 24 } })
    const extra = await Promise.allSettled(Array.from({ length: 8 }, () => reserve(db, "org_one")))
    assert.equal(extra.filter(result => result.status === "fulfilled").length, 4)
    assert.equal((await db.collection("usage").findOne({ organisation_id: "org_one", day }))?.calls, 24)
    await reserve(db, "org_other")
    assert.equal((await db.collection("usage").findOne({ organisation_id: "org_other", day }))?.limit, 20)
  } finally { await client.close(); await server.stop() }
})
