import type { DbExec, DbExecStatement } from "@agent-native/core/db"
import {
  BeginTransactionCommand, CommitTransactionCommand, ExecuteStatementCommand,
  RDSDataClient, RollbackTransactionCommand, type SqlParameter,
} from "@aws-sdk/client-rds-data"
import { bindSql } from "./sql"

export interface DatabaseOptions { cluster: string, secret: string, database: string }

export function dataApi(options: DatabaseOptions, client = new RDSDataClient({ maxAttempts: 2 })): DbExec {
  const base = { resourceArn: options.cluster, secretArn: options.secret, database: options.database }
  const execute = async (statement: DbExecStatement, transactionId?: string) => {
    const query = typeof statement === "string" ? { sql: statement, args: [] } : statement
    const args = query.args ?? []
    const sql = bindSql(query.sql, args.length)
    for (let attempt = 0; ; attempt++) {
      try {
        const response = await client.send(new ExecuteStatementCommand({
          ...base, sql, parameters: args.map(parameter), transactionId,
          formatRecordsAs: "JSON", resultSetOptions: { longReturnType: "LONG", decimalReturnType: "STRING" },
        }))
        return { rows: JSON.parse(response.formattedRecords ?? "[]") as Record<string, unknown>[], rowsAffected: response.numberOfRecordsUpdated ?? 0 }
      } catch (error) {
        if (!transactionId && attempt < 4 && error instanceof Error && error.name === "DatabaseResumingException") {
          await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)))
          continue
        }
        throw new Error("Native credential storage is unavailable")
      }
    }
  }
  return {
    execute: (query) => execute(query),
    transaction: async (run) => {
      const { transactionId } = await client.send(new BeginTransactionCommand(base))
      if (!transactionId) throw new Error("Native storage transaction did not start")
      let queued = Promise.resolve()
      const tx: DbExec = {
        execute: (query) => {
          const result = queued.then(() => execute(query, transactionId))
          queued = result.then(() => undefined)
          return result
        },
      }
      try {
        const result = await run(tx)
        await queued
        await client.send(new CommitTransactionCommand({ ...base, transactionId }))
        return result
      } catch (error) {
        await client.send(new RollbackTransactionCommand({ ...base, transactionId })).catch(() => undefined)
        throw error
      }
    },
  }
}

function parameter(value: unknown, index: number): SqlParameter {
  const name = `p${index + 1}`
  if (value === null || value === undefined) return { name, value: { isNull: true } }
  if (typeof value === "boolean") return { name, value: { booleanValue: value } }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Native SQL number is invalid")
    return { name, value: Number.isSafeInteger(value) ? { longValue: value } : { doubleValue: value } }
  }
  if (typeof value === "string") return { name, value: { stringValue: value } }
  if (value instanceof Date) return { name, value: { stringValue: value.toISOString() } }
  throw new Error("Native SQL parameter type is unsupported")
}
