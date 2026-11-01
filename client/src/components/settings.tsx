import type { ReactNode } from "react"
import { cn } from "../lib/utils"

const columns = "sm:grid-cols-[128px_minmax(0,1fr)] sm:gap-x-6"

export function SettingsRow({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return <div className={cn("grid min-h-[68px] gap-y-2 py-4 sm:items-center", columns)}>
    <dt className="text-[11px] font-medium text-[var(--ink-soft)]">{htmlFor ? <label htmlFor={htmlFor}>{label}</label> : label}</dt>
    <dd className="min-w-0 break-words text-[11px] font-medium text-[var(--ink)]">{children}</dd>
  </div>
}

export function SettingsFooter({ children }: { children: ReactNode }) {
  return <div className={cn("grid pt-5", columns)}><div className="min-w-0 sm:col-start-2">{children}</div></div>
}
