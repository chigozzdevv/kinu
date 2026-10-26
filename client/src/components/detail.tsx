import { Pencil } from "lucide-react"
import type { ReactNode } from "react"

export function DetailTabs<T extends string>({ items, value, onChange }: { items: ReadonlyArray<{ id: T; label: string }>; value: T; onChange: (value: T) => void }) {
  return <div className="mb-6 flex gap-1">
    {items.map((item) => <button key={item.id} className={`focus-ring border-b-2 px-4 py-3 text-[11px] font-semibold ${value === item.id ? "border-[var(--ink)] text-[var(--ink)]" : "border-transparent text-[var(--ink-muted)] hover:text-[var(--ink)]"}`} onClick={() => onChange(item.id)}>{item.label}</button>)}
  </div>
}

export function DetailCard({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-[var(--border)] bg-white p-6">
      {title && <h3 className="eyebrow mb-4">{title}</h3>}
      {children}
    </section>
  )
}

export function DetailList({ children }: { children: ReactNode }) {
  return <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">{children}</dl>
}

export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 text-[11px]">
      <dt className="text-[9px] font-medium text-[var(--ink-muted)]">{label}</dt>
      <dd className="mt-1 min-w-0 break-words font-medium text-[var(--ink)]">{children}</dd>
    </div>
  )
}

export function Section({ title, children, onEdit }: { title: string; children: ReactNode; onEdit?: () => void }) {
  return (
    <section className="mb-7 last:mb-0">
      <div className="mb-3 flex min-h-7 items-center justify-between gap-3">
        <h3 className="eyebrow">{title}</h3>
        {onEdit && <button type="button" className="focus-ring grid size-7 place-items-center rounded-lg text-[var(--ink-muted)] transition hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" onClick={onEdit} aria-label={`Edit ${title}`} title={`Edit ${title}`}><Pencil className="size-3.5" /></button>}
      </div>
      {children}
    </section>
  )
}
