import type { ReactNode } from "react"

export function SettingsRow({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return <div className="grid min-h-[68px] gap-y-2 py-4 sm:grid-cols-[128px_minmax(0,1fr)] sm:items-center sm:gap-x-6">
    <dt className="text-[11px] font-medium text-[var(--ink-soft)]">{htmlFor ? <label htmlFor={htmlFor}>{label}</label> : label}</dt>
    <dd className="min-w-0 break-words text-[11px] font-medium text-[var(--ink)]">{children}</dd>
  </div>
}

export function SettingsFooter({ children }: { children: ReactNode }) {
  return <div className="pt-5">{children}</div>
}
