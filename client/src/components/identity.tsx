import { Mail, ShieldCheck } from "lucide-react"
import type { ReactNode } from "react"

export function IdentityProvider({ value }: { value: string }) {
  const normalized = value.toLowerCase()

  if (normalized.includes("email") || normalized.includes("password")) return <Mark icon={<Mail className="size-4" strokeWidth={1.8} />} label={value} />
  return <Mark icon={<ShieldCheck className="size-4" strokeWidth={1.8} />} label={value} />
}

function Mark({ icon, label }: { icon: ReactNode; label: string }) {
  return <span className="inline-flex items-center gap-2"><span className="grid size-5 place-items-center" aria-hidden="true">{icon}</span><span>{label}</span></span>
}
