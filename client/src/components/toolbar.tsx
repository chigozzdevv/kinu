import { ChevronDown, Search, X } from "lucide-react"
import type { SelectHTMLAttributes } from "react"

type ToolbarFilter = SelectHTMLAttributes<HTMLSelectElement> & { label: string; defaultValue?: string }

export function Toolbar({ value, onChange, placeholder, filters, onClear }: { value: string; onChange: (value: string) => void; placeholder: string; filters?: ToolbarFilter[]; onClear?: () => void }) {
  const active = Boolean(value.trim()) || Boolean(filters?.some((filter) => String(filter.value ?? "") !== String(filter.defaultValue ?? "all")))

  return (
    <div className="resource-toolbar">
      <div className="flex flex-wrap items-center gap-2.5">
      <label className="relative min-w-[240px] flex-1">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-[var(--ink-muted)]" strokeWidth={2} />
        <input
          className="focus-ring h-10 w-full rounded-xl border border-[var(--border)] bg-[var(--surface-soft)] pl-10 pr-4 text-[12px] text-[var(--ink)] placeholder:text-[var(--ink-muted)]"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
        />
      </label>

      {filters?.map(({ label, children, defaultValue: _defaultValue, ...props }) => (
                <label key={label} className="relative block shrink-0">
                  <span className="sr-only">{label}</span>
                    <select
                      aria-label={label}
                      className="focus-ring h-10 min-w-36 appearance-none rounded-xl border border-[var(--border)] bg-[var(--surface-soft)] px-3 pr-8 text-[11px] font-medium text-[var(--ink)]"
                      {...props}
                    >
                      {children}
                    </select>
                    <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-[var(--ink-muted)]" />
                </label>
      ))}
      {active && onClear && <button className="focus-ring inline-flex h-10 items-center gap-1.5 rounded-xl px-3 text-[10px] font-semibold text-[var(--ink-soft)] transition hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" onClick={onClear}><X className="size-3.5" /> Clear</button>}
      </div>
    </div>
  )
}
