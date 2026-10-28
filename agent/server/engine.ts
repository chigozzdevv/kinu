import {
  BUILDER_CAPABILITIES, BUILDER_DEFAULT_MODEL, BUILDER_SUPPORTED_MODELS,
  type AgentEngine,
} from "@agent-native/core/agent/engine"
import { gatewayEvents } from "./stream"

const gateway = "https://api.builder.io/agent-native/gateway/v1/messages"

export function builderEngine(accessToken: string, request: typeof fetch = fetch): AgentEngine {
  return {
    name: "builder", label: "Builder.io", defaultModel: BUILDER_DEFAULT_MODEL,
    supportedModels: BUILDER_SUPPORTED_MODELS, capabilities: BUILDER_CAPABILITIES,
    async *stream(options) {
      if (options.tools.length || !BUILDER_SUPPORTED_MODELS.some((model) => model === options.model)) {
        throw new Error("Builder accepts a supported model without tools")
      }
      const messages = options.messages.map((message) => ({ role: message.role,
        content: message.content.map((part) => {
          if (part.type === "text") return { type: "text", text: part.text }
          if (part.type === "image") return { type: "image", source: {
            type: "base64", data: part.data, media_type: part.mediaType,
          } }
          throw new Error("Builder accepts text and redacted images only")
        }),
      }))
      const requested = options.maxOutputTokens ?? 256
      if (!Number.isInteger(requested) || requested <= 0 || requested > 2048) throw new Error("Builder output budget is invalid")
      const body = JSON.stringify({ model: options.model, messages, system: options.systemPrompt,
        max_tokens: Math.max(256, requested),
      })
      if (Buffer.byteLength(body) > 1_100_000) throw new Error("Builder request is too large")
      const response = await request(new Request(gateway, { method: "POST", redirect: "error",
        headers: { authorization: "Bearer " + accessToken, "content-type": "application/json",
          accept: "application/x-ndjson", "x-client-name": "kinu" },
        body, signal: options.abortSignal,
      }))
      if (!response.ok || !response.body || response.headers.get("content-type")?.includes("text/html")) {
        await response.body?.cancel()
        throw new Error("Builder gateway request failed (" + response.status + ")")
      }
      yield* gatewayEvents(response.body)
    },
  }
}
