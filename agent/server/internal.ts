import { timingSafeEqual } from "node:crypto"
import { readFileSync } from "node:fs"
import { z } from "zod"
import { assessment } from "./assessment"
import { inputSchema } from "./decision"
import { navigationResponse, navigationSchema } from "./navigation"
import { playbookResponse, playbookSchema } from "./playbooks"
import { planInput, rotationPlanResponse } from "./rotation-plan"

export async function internalResponse(request: Request): Promise<Response> {
  const token = process.env.KINU_NATIVE_TOKEN_FILE
    ? readFileSync(process.env.KINU_NATIVE_TOKEN_FILE, "utf8").trim() : process.env.KINU_NATIVE_TOKEN
  const expected = Buffer.from("Bearer " + (token ?? ""))
  const supplied = Buffer.from(request.headers.get("authorization") ?? "")
  if (!token || expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 })
  }
  const path = new URL(request.url).pathname
  if (!["/_internal/assessment", "/_internal/navigation", "/_internal/playbooks", "/_internal/rotation-plan"].includes(path) || request.method !== "POST") {
    return Response.json({ error: "Not found" }, { status: 404 })
  }
  const text = await request.text()
  if (Buffer.byteLength(text) > (path === "/_internal/playbooks" ? 256000 : path === "/_internal/navigation" ? 800000 : 16384)) return Response.json({ error: "Request too large" }, { status: 413 })
  if (path === "/_internal/rotation-plan") {
    const body = z.strictObject({ organisation_id: z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/), input: planInput }).safeParse(JSON.parse(text))
    if (!body.success) return Response.json({ error: "Invalid rotation plan request" }, { status: 422 })
    return rotationPlanResponse(body.data.organisation_id, body.data.input, request.signal)
  }
  if (path === "/_internal/playbooks") {
    const body = z.strictObject({ organisation_id: z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/), input: playbookSchema }).safeParse(JSON.parse(text))
    if (!body.success) return Response.json({ error: "Invalid playbook instructions" }, { status: 422 })
    return playbookResponse(body.data.organisation_id, body.data.input, request.signal)
  }
  if (path === "/_internal/navigation") {
    const body = z.strictObject({ organisation_id: z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/), input: navigationSchema }).safeParse(JSON.parse(text))
    if (!body.success) return Response.json({ error: "Invalid browser inference" }, { status: 422 })
    return navigationResponse(body.data.organisation_id, body.data.input, request.signal)
  }
  const body = z.strictObject({ organisation_id: z.string().regex(/^[a-z][a-z0-9_-]{2,127}$/), input: inputSchema })
    .safeParse(JSON.parse(text))
  if (!body.success) return Response.json({ error: "Invalid assessment" }, { status: 422 })
  return Response.json(await assessment(body.data.organisation_id, body.data.input), {
    headers: { "cache-control": "no-store" },
  })
}
