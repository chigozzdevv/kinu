import assert from "node:assert/strict"
import test from "node:test"
import { RDSDataClient } from "@aws-sdk/client-rds-data"
import { dataApi } from "../server/database"

test("native Data API binds values separately and rolls back failed transactions", async () => {
  const calls: Array<Record<string, unknown>> = []
  const client = new RDSDataClient({
    region: "eu-west-2", credentials: { accessKeyId: "test", secretAccessKey: "test" }, maxAttempts: 1,
    requestHandler: { handle: async (request: { body?: unknown, path: string }) => {
      const body = JSON.parse(request.body as string) as Record<string, unknown>
      calls.push(body)
      const operation = request.path
      const result = operation.endsWith("BeginTransaction") ? { transactionId: "transaction-one" } : { formattedRecords: '[{"ready":true}]', numberOfRecordsUpdated: 1 }
      return { response: { statusCode: 200, headers: { "content-type": "application/json" }, body: Buffer.from(JSON.stringify(result)) } }
    } },
  })
  const db = dataApi({ cluster: "cluster", secret: "native-secret", database: "kinu_native" }, client)
  const privateValue = "value'); DROP TABLE settings; --"
  const result = await db.execute({ sql: "SELECT ? AS value", args: [privateValue] })
  assert.deepEqual(result.rows, [{ ready: true }])
  assert.equal(calls[0]!.sql, "SELECT :p1 AS value")
  assert.deepEqual(calls[0]!.parameters, [{ name: "p1", value: { stringValue: privateValue } }])
  assert.equal(calls[0]!.database, "kinu_native")
  await assert.rejects(db.transaction!(async (tx) => {
    await tx.execute("SELECT 1")
    throw new Error("stop before commit")
  }), /stop before commit/)
  assert.equal(calls.filter((call) => call.transactionId === "transaction-one").length, 2)
  client.destroy()
})
