import type { HttpProviderApi } from "../types"

export function ApiOperations({ definition }: { definition: HttpProviderApi }) {
  return <div className="divide-y divide-[var(--border-soft)]">{([
    ["List", definition.list_credentials], ["Create", definition.create_credential],
    ["Verify", definition.test_credential], ["Revoke", definition.revoke_credential],
  ] as const).map(([label, operation]) => operation && <div key={label} className="grid gap-1 py-3 text-[11px] sm:grid-cols-[64px_1fr]">
    <span className="font-medium">{label}</span><code className="break-all text-[10px] text-[var(--ink-soft)]">{operation.method} {operation.path}</code>
  </div>)}</div>
}
