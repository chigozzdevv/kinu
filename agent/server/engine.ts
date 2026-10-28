import {
  createAISDKEngine, BUILDER_CAPABILITIES, BUILDER_DEFAULT_MODEL, BUILDER_SUPPORTED_MODELS,
  type AgentEngine,
} from "@agent-native/core/agent/engine"

const gateway = "https://api.builder.io/agent-native/gateway/v1"

export function builderEngine(accessToken: string, request: typeof fetch = fetch): AgentEngine {
  return createAISDKEngine("anthropic", {
    name: "builder", label: "Builder.io", model: BUILDER_DEFAULT_MODEL,
    supportedModels: BUILDER_SUPPORTED_MODELS, capabilities: BUILDER_CAPABILITIES,
    apiKey: accessToken, allowEnvFallback: false, baseUrl: gateway, skipCredentialFailureTracking: true,
    requestFetch: async (input: RequestInfo | URL, init?: RequestInit) => {
      const call = new Request(input, init)
      if (call.url !== gateway + "/messages") throw new Error("Unexpected model destination")
      const headers = new Headers(call.headers)
      headers.delete("x-api-key")
      headers.set("authorization", "Bearer " + accessToken)
      headers.set("x-client-name", "kinu")
      return request(new Request(call, { headers, redirect: "error" }))
    },
  })
}
