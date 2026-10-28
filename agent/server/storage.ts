import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto"
import { readFileSync } from "node:fs"
import { MongoClient, type Db } from "mongodb"

let initialized: Promise<Db> | undefined

async function initialize(): Promise<Db> {
  const url = process.env.KINU_NATIVE_MONGO_URL
  if (!url) throw new Error("Builder connection storage is not configured")
  const client = new MongoClient(url, { serverSelectionTimeoutMS: 5000 })
  await client.connect()
  const db = client.db(process.env.KINU_NATIVE_DATABASE ?? "kinu_agent")
  await Promise.all([
    db.collection("connections").createIndex({ organisation_id: 1 }, { unique: true }),
    db.collection("pending").createIndex({ state_hash: 1 }, { unique: true }),
    db.collection("pending").createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 }),
    db.collection("usage").createIndex({ organisation_id: 1, day: 1 }, { unique: true }),
  ])
  return db
}

export async function withStorage<T>(run: (db: Db) => Promise<T>): Promise<T> {
  initialized ??= initialize().catch((error: unknown) => { initialized = undefined; throw error })
  return run(await initialized)
}

function key(): Buffer {
  const file = process.env.KINU_NATIVE_KEY_FILE
  const material = file ? readFileSync(file, "utf8").trim() : process.env.KINU_NATIVE_KEY
  if (!material || material.length < 32) throw new Error("Native encryption key is unavailable")
  return createHash("sha256").update(material).digest()
}

export function encrypt(value: unknown, context: string): string {
  const iv = randomBytes(12), cipher = createCipheriv("aes-256-gcm", key(), iv)
  cipher.setAAD(Buffer.from(context))
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()])
  return [iv, cipher.getAuthTag(), encrypted].map((part) => part.toString("base64url")).join(".")
}

export function decrypt<T>(value: string, context: string): T {
  const parts = value.split(".").map((part) => Buffer.from(part, "base64url"))
  if (parts.length !== 3) throw new Error("Native connection state is invalid")
  const decipher = createDecipheriv("aes-256-gcm", key(), parts[0]!)
  decipher.setAAD(Buffer.from(context))
  decipher.setAuthTag(parts[1]!)
  return JSON.parse(Buffer.concat([decipher.update(parts[2]!), decipher.final()]).toString()) as T
}

export const hash = (value: string): string => createHash("sha256").update(value).digest("hex")
