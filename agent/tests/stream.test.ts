import assert from "node:assert/strict"
import { test } from "node:test"
import { gatewayEvents } from "../server/stream"

const encoder = new TextEncoder()
function stream(value: string, size = 7) {
  const bytes = encoder.encode(value)
  let offset = 0
  return new ReadableStream<Uint8Array>({ pull(controller) {
    if (offset === bytes.length) return controller.close()
    const next = Math.min(bytes.length, offset + size)
    controller.enqueue(bytes.slice(offset, next))
    offset = next
  } })
}

async function collect(body: ReadableStream<Uint8Array>) {
  const events = []
  for await (const event of gatewayEvents(body)) events.push(event)
  return events
}

test("Builder JSONL handles chunked UTF-8, heartbeats, usage and a final line without newline", async () => {
  const wire = [{ type: "heartbeat" }, { type: "text-delta", text: "héllo" },
    { type: "usage", inputTokens: 2, outputTokens: 1, creditsUsed: 0.01 },
    { type: "stop", reason: "end_turn" }].map((item) => JSON.stringify(item)).join("\n")
  assert.deepEqual(await collect(stream(wire)), [
    { type: "gateway-heartbeat" }, { type: "text-delta", text: "héllo" },
    { type: "usage", inputTokens: 2, outputTokens: 1, builderCreditsUsed: 0.01 },
    { type: "stop", reason: "end_turn" },
  ])
})

test("Builder streams fail closed on truncation, tool calls and invalid usage", async () => {
  for (const wire of [JSON.stringify({ type: "text-delta", text: "partial" }),
    JSON.stringify({ type: "tool-call", name: "unsafe" }) + "\n",
    JSON.stringify({ type: "usage", creditsUsed: -1 }) + "\n", "event: message_stop\n\n",
    "x".repeat(65537)]) {
    await assert.rejects(collect(stream(wire, 4096)))
  }
  assert.deepEqual(await collect(stream('{"type":"stop","reason":"invalid_request","errorCode":"model_unavailable"}')),
    [{ type: "stop", reason: "error", errorCode: "model_unavailable" }])
})

test("long token streams within the playbook budget still complete", async () => {
  const wire = Array.from({ length: 3000 }, (_, index) => JSON.stringify({ type: "text-delta", text: String(index % 10) }))
    .concat(JSON.stringify({ type: "stop", reason: "end_turn" })).join("\n")
  const events = await collect(stream(wire, 4096))
  assert.equal(events.length, 3001)
  assert.equal(events.at(-1)?.type, "stop")
})
