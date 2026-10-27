import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto"
import { withDbExec, type DbExec } from "@agent-native/core/db"
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager"
import { dataApi } from "./database"

let initialized: Promise<DbExec> | undefined

async function initialize(): Promise<DbExec> {
  const cluster = process.env.KINU_NATIVE_CLUSTER_ARN
  const secret = process.env.KINU_NATIVE_SECRET_ARN
  const seal = process.env.KINU_NATIVE_SEAL_ARN
  if (!cluster || !secret || !seal) throw new Error("Builder connection storage is not configured")
  const response = await new SecretsManagerClient({ maxAttempts: 2 }).send(new GetSecretValueCommand({ SecretId: seal }))
  if (!response.SecretString || response.SecretString.length < 32) throw new Error("Native encryption key is unavailable")
  process.env.APP_NAME = "kinu-agent"
  process.env.KINU_AGENT_SECRETS_ENCRYPTION_KEY = response.SecretString
  return dataApi({ cluster, secret, database: "kinu_native" })
}

export async function withStorage<T>(run: (db: DbExec) => Promise<T>): Promise<T> {
  initialized ??= initialize().catch((error: unknown) => { initialized = undefined; throw error })
  const db = await initialized
  return withDbExec(db, () => run(db))
}

function key(): Buffer {
  const material = process.env.KINU_AGENT_SECRETS_ENCRYPTION_KEY
  if (!material || material.length < 32) throw new Error("Native encryption key is unavailable")
  return createHash("sha256").update(material).digest()
}

export function encrypt(value: unknown): string {
  const iv = randomBytes(12), cipher = createCipheriv("aes-256-gcm", key(), iv)
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()])
  return [iv, cipher.getAuthTag(), encrypted].map((part) => part.toString("base64url")).join(".")
}

export function decrypt(value: string): unknown {
  const parts = value.split(".").map((part) => Buffer.from(part, "base64url"))
  if (parts.length !== 3) throw new Error("Native connection state is invalid")
  const decipher = createDecipheriv("aes-256-gcm", key(), parts[0]!)
  decipher.setAuthTag(parts[1]!)
  return JSON.parse(Buffer.concat([decipher.update(parts[2]!), decipher.final()]).toString())
}

export const hash = (value: string): string => createHash("sha256").update(value).digest("hex")
