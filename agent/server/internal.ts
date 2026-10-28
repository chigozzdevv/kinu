import { timingSafeEqual } from "node:crypto"
import { readFileSync } from "node:fs"
import { z } from "zod"
import { assessment } from "./assessment"
import { inputSchema } from "./decision"

export async function internalResponse(request: Request): Promise<Response> {
  const token = process.env.KINU_NATIVE_TOKEN_FILE
    ? readFileSync(process.env.KINU_NATIVE_TOKEN_FILE, "utf8").trim() : process.env.KINU_NATIVE_TOKEN
  const expected = Buffer.from("Bearer " + (token ?? ""))
  const supplied = Buffer.from(request.headers.get("authorization") ?? "")
  if (!token || expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 })
  }
  if (new URL(request.url).pathname !== "/_internal/assessment" || request.method !== "POST") {
    return Response.json({ error: "Not found" }, { status: 404 })
  }
  const text = await request.text()
  if (Buffer.byteLength(text) > 16384) return Response.json({ error: "Request too large" }, { status: 413 })
  const body = z.strictObject({ organisation_id: z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/), input: inputSchema })
    .safeParse(JSON.parse(text))
  if (!body.success) return Response.json({ error: "Invalid assessment" }, { status: 422 })
  return Response.json(await assessment(body.data.organisation_id, body.data.input), {
    headers: { "cache-control": "no-store" },
  })
}
