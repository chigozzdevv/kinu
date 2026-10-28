import type { EngineEvent } from "@agent-native/core/agent/engine"
import { z } from "zod"

const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER).default(0)
const eventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("text-delta"), text: z.string().max(16384) }),
  z.object({ type: z.literal("heartbeat") }),
  z.object({ type: z.enum(["thinking-delta", "reasoning-delta"]), text: z.string().max(16384) }),
  z.object({ type: z.literal("usage"), inputTokens: count, outputTokens: count,
    creditsUsed: z.number().finite().nonnegative().optional() }),
  z.object({ type: z.literal("stop"), reason: z.string(), errorCode: z.string().max(64).optional() }),
])

export async function* gatewayEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<EngineEvent> {
  const reader = body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true })
  let pending = "", bytes = 0, lines = 0
  function parse(line: string): EngineEvent | null {
    if (++lines > 2048 || line.length > 32768) throw new Error("Builder stream exceeded its boundary")
    const event = eventSchema.parse(JSON.parse(line))
    if (event.type === "text-delta") return event
    if (event.type === "heartbeat") return { type: "gateway-heartbeat" }
    if (event.type === "usage") return { type: "usage", inputTokens: event.inputTokens,
      outputTokens: event.outputTokens, ...(event.creditsUsed !== undefined ? { builderCreditsUsed: event.creditsUsed } : {}) }
    if (event.type === "stop") return { type: "stop",
      reason: event.reason === "end_turn" || event.reason === "stop_sequence" || event.reason === "max_tokens"
        ? event.reason : "error",
      ...(event.errorCode && /^[a-z0-9_.-]+$/i.test(event.errorCode) ? { errorCode: event.errorCode } : {}),
    }
    return null
  }
  try {
    while (true) {
      const chunk = await reader.read()
      bytes += chunk.value?.byteLength ?? 0
      if (bytes > 262144) throw new Error("Builder stream exceeded its boundary")
      pending += decoder.decode(chunk.value, { stream: !chunk.done })
      let newline: number
      while ((newline = pending.indexOf("\n")) >= 0 || (chunk.done && pending.length)) {
        const line = (newline >= 0 ? pending.slice(0, newline) : pending).trim()
        pending = newline >= 0 ? pending.slice(newline + 1) : ""
        if (!line) continue
        const event = parse(line)
        if (event) yield event
        if (event?.type === "stop") return
      }
      if (pending.length > 32768) throw new Error("Builder stream exceeded its boundary")
      if (chunk.done) throw new Error("Builder stream ended before completion")
    }
  } finally {
    try { await reader.cancel() } finally { reader.releaseLock() }
  }
}
