import assert from "node:assert/strict"
import test from "node:test"
import { apiSourceError, linkError, parseLinks } from "../src/lib/playbook-source.ts"

test("API instructions accept prose, docs links, and valid request/response JSON", () => {
  for (const source of [
    "Use POST /keys to create a key, then GET /identity to verify it.",
    "[API docs](https://docs.example.com/keys)",
    '{"input":{"name":"replacement"},"output":{"id":"key_123","token":"example"}}',
    '[{"id":"key_123"}]',
    'Request:\n```json\n{"name":"replacement"}\n```\nResponse:\n```json\n{"id":"key_123"}\n```',
    'Example:\n```\n{"active":true}\n```',
    '```bash\ncurl https://api.example.com/keys\n```',
  ]) assert.equal(apiSourceError(source), undefined)
})

test("malformed API JSON is identified before generation, including a bad output example", () => {
  for (const source of ['{"name":}', '[{"id":"key_123"},]', '```json\n{"id":"key_123",}\n```', '```json\n\n```']) {
    assert.match(apiSourceError(source), /JSON is invalid/)
  }
  assert.match(apiSourceError('Request:\n```json\n{"name":"replacement"}\n```\nResponse:\n```json\n{"id":}\n```'), /JSON example 2 is invalid/)
})

test("link sources accept newlines, commas and spaces, and reject non-https or excessive links", () => {
  assert.deepEqual(parseLinks("https://a.example/x, https://b.example/y"), ["https://a.example/x", "https://b.example/y"])
  assert.deepEqual(parseLinks("https://a.example/x\nhttps://b.example/y"), ["https://a.example/x", "https://b.example/y"])
  assert.deepEqual(parseLinks("https://a.example/x https://b.example/y"), ["https://a.example/x", "https://b.example/y"])
  assert.deepEqual(parseLinks("https://a.example/x, https://a.example/x"), ["https://a.example/x"])
  assert.equal(linkError("https://resend.com/docs/create-an-api-key, https://resend.com/docs/dashboard/api-keys/introduction"), undefined)
  assert.match(linkError("not-a-link") ?? "", /https/)
  assert.match(linkError(Array.from({ length: 6 }, (_, index) => `https://a.example/${index}`).join("\n")) ?? "", /five/)
})
