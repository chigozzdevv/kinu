import assert from "node:assert/strict"
import test from "node:test"
import { loadConfig } from "../server/config"
import { builderCallback, builderCallbackUrl } from "../server/builder"
import { callbackPath } from "../server/grant"
import { withCors } from "../server/cors"

const config = loadConfig({ KINU_PUBLIC_URL: "https://kinu.web.app", KINU_API_PUBLIC_URL: "https://api.example.com", KINU_ENVIRONMENT: "production" })

test("Firebase preflights require the exact frontend origin, method and headers", async () => {
  for (const [origin, headers, expected] of [
    [config.publicUrl, "content-type,x-csrf-token,x-kinu-workspace", 204],
    ["https://attacker.example", "content-type", 403],
    [config.publicUrl, "authorization", 403],
  ] as const) {
    const response = await withCors(new Request(config.publicApiUrl + "/_agent-native/kinu/builder/connect", {method:"OPTIONS", headers:{origin, "access-control-request-method":"POST", "access-control-request-headers":headers}}), config, async () => { throw new Error("Preflight must not authenticate or mutate") })
    assert.equal(response.status, expected)
    assert.equal(response.headers.get("access-control-allow-origin"), expected === 204 ? config.publicUrl : null)
  }
})

test("CORS preserves authenticated route failures and private route boundaries", async () => {
  const response = await withCors(new Request(config.publicApiUrl + "/_agent-native/kinu/builder/status", {headers:{origin:config.publicUrl}}), config, async () => Response.json({error:"Sign in"}, {status:401}))
  assert.equal(response.status, 401)
  assert.equal(response.headers.get("access-control-allow-credentials"), "true")
  const privateResponse = await withCors(new Request(config.publicApiUrl + "/_internal/playbooks", {headers:{origin:config.publicUrl}}), config, async () => new Response(null, {status:401}))
  assert.equal(privateResponse.headers.get("access-control-allow-origin"), null)
  assert.equal(config.publicApiUrl, "https://api.example.com")
  assert.equal(config.cookieName, "__Host-kinu")
})


test("Builder callbacks use the public API address rather than the Firebase frontend", async () => {
  await assert.rejects(builderCallback(new Request(config.publicApiUrl + callbackPath), config), {status:401})
  await assert.rejects(builderCallback(new Request(config.publicUrl + callbackPath), config), {status:400})
})

test("Builder callbacks preserve the HTTPS redirect through the private gateway", async () => {
  const headers = {"x-forwarded-proto":"https", "x-forwarded-host":"api.example.com"}
  const request = new Request("http://api.example.com" + callbackPath + "?state=opaque&code=grant", {headers})
  assert.equal(builderCallbackUrl(request, config).href, config.publicApiUrl + callbackPath + "?state=opaque&code=grant")
  await assert.rejects(builderCallback(request, config), {status:401})
  for (const [origin, forwarding, query] of [
    ["http://attacker.example", headers, ""],
    ["http://api.example.com", {}, ""],
    ["http://api.example.com", {...headers, "x-forwarded-host":"attacker.example"}, ""],
    ["http://api.example.com", {...headers, "x-forwarded-proto":"https,http"}, ""],
    ["http://api.example.com", headers, "?code=one&code=two"],
    ["http://api.example.com", headers, "?redirect_uri=https://attacker.example"],
  ] as const) {
    assert.throws(() => builderCallbackUrl(new Request(origin + callbackPath + query, {headers:forwarding}), config), {status:400})
  }
})
