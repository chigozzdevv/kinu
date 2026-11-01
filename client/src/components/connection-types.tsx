import { ChevronRight, Inbox, KeyRound, LockKeyhole, Server } from "lucide-react"
import type { ConnectionRole } from "../types"

const types = [
  { id: "runtime", name: "Hosting", icon: Server },
  { id: "provider", name: "Providers", icon: KeyRound },
  { id: "incident", name: "Incident sources", icon: Inbox },
  { id: "secret-store", name: "Secret stores", icon: LockKeyhole },
] as const

export function ConnectionTypes({ onSelect }: { onSelect: (role: ConnectionRole) => void }) {
  return <div className="grid gap-3 sm:grid-cols-2">
    {types.map(({ id, name, icon: Icon }) => <button key={id} type="button" onClick={() => onSelect(id)}
      className="focus-ring flex items-center gap-4 rounded-xl border border-[var(--border)] bg-white p-5 text-left hover:border-[var(--ink-muted)]">
      <Icon className="size-5 text-[var(--ink-soft)]" strokeWidth={1.6} />
      <span className="flex-1 text-[13px] font-medium">{name}</span><ChevronRight className="size-4 text-[var(--ink-muted)]" />
    </button>)}
  </div>
}
