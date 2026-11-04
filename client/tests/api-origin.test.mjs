import assert from "node:assert/strict"
import test from "node:test"
import { normaliseApiOrigin, apiUrl, browserGatewayUrl } from "../src/lib/api-origin.ts"

test("API configuration accepts HTTPS origins and rejects credential leaks or path injection", () => {
  assert.equal(normaliseApiOrigin("https://api.example.com/"), "https://api.example.com")
  assert.equal(normaliseApiOrigin(""), "")
  assert.equal(normaliseApiOrigin("http://localhost:8000", true), "http://localhost:8000")
  for (const value of ["http://public.example.com", "https://user:password@api.example.com", "https://api.example.com/private", "https://api.example.com?secret=value"]) assert.throws(() => normaliseApiOrigin(value))
  assert.equal(apiUrl("/auth/session"), "/auth/session")
  for (const path of ["https://attacker.example", "//attacker.example", "/\\attacker.example"]) assert.throws(() => apiUrl(path))
})

test("browser sessions can use the configured API host and reject unrelated gateways", () => {
  assert.equal(browserGatewayUrl("https://app.example.com/browser-gateway", "https://app.example.com"), "wss://app.example.com/browser-gateway/v1/setup/live")
  assert.equal(browserGatewayUrl("https://api.example.com/browser-gateway", "https://kinu.web.app", "https://api.example.com"), "wss://api.example.com/browser-gateway/v1/setup/live")
  assert.throws(() => browserGatewayUrl("https://attacker.example/browser-gateway", "https://kinu.web.app", "https://api.example.com"))
  assert.throws(() => browserGatewayUrl("https://user:password@api.example.com/browser-gateway", "https://kinu.web.app", "https://api.example.com"))
})
