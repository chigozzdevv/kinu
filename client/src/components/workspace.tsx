import { ChevronDown, X } from "lucide-react"
import { useEffect, useRef, useState, type ReactNode, type SelectHTMLAttributes } from "react"
import { PageHeader } from "./header"
import { Button } from "./ui/button"
import { cn } from "../lib/utils"

const setupContent = "mx-auto w-full max-w-[760px] px-6 sm:px-8"

function useTransientError(error?: string) {
  const [visible, setVisible] = useState(error)

  useEffect(() => {
    setVisible(error)
    if (!error) return
    const timeout = window.setTimeout(() => setVisible(undefined), 5_000)
    return () => window.clearTimeout(timeout)
  }, [error])

  return visible
}

export function SetupPage({
  eyebrow,
  title,
  description,
  steps,
  current,
  onBack,
  onExit,
  onCancel,
  primary,
  children,
  error,
}: {
  eyebrow: string
  title: string
  description?: string
  steps: string[]
  current: number
  onBack: () => void
  onExit?: () => void
  onCancel: () => void
  primary: ReactNode
  children: ReactNode
  error?: string
}) {
  const visibleError = useTransientError(error)

  return (
    <div className="page max-w-[960px]">
      <PageHeader eyebrow={eyebrow} title={title} description={description} onBack={onExit ?? onCancel} />
      <section className="overflow-hidden rounded-2xl border border-[var(--border)] bg-white">
        <div className={cn(setupContent, "flex items-center justify-between pt-6")}>
          <span className="text-[11px] font-semibold text-[var(--ink)]">{steps[current]}</span>
          <span className="text-[10px] font-medium text-[var(--ink-muted)]">Step {current + 1} of {steps.length}</span>
        </div>
        <div className={cn(setupContent, "min-h-[360px] py-7")}>{children}</div>
        {visibleError && <div className={cn(setupContent, "pb-5")}><div role="alert" className="rounded-xl border border-[#ebcfd3] bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{visibleError}</div></div>}
        <footer className={cn(setupContent, "flex flex-wrap items-center justify-between gap-3 bg-white py-4")}>
          {current > 0 && <Button variant="ghost" onClick={onBack}>Back</Button>}
          <div className="ml-auto flex max-w-full flex-wrap justify-end gap-3">
            <Button variant="secondary" onClick={onCancel}>Cancel</Button>
            {primary}
          </div>
        </footer>
      </section>
    </div>
  )
}

export function ConnectPage({
  eyebrow,
  title,
  onBack,
  onClose,
  action,
  children,
  error,
}: {
  eyebrow: string
  title: string
  onBack: () => void
  onClose: () => void
  action: ReactNode
  children: ReactNode
  error?: string
}) {
  const visibleError = useTransientError(error)

  return <div className="page max-w-[960px]">
    <PageHeader
      eyebrow={eyebrow}
      title={title}
      onBack={onBack}
      actions={<button className="focus-ring grid size-8 place-items-center rounded-lg text-[var(--ink-muted)] transition hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" aria-label="Close" onClick={onClose}><X className="size-4" /></button>}
    />
    <section className="rounded-2xl border border-[var(--border)] bg-white">
      <div className="flex min-h-[420px] items-center justify-center px-6 py-12">
        <div className="flex flex-col items-center gap-7">{children}{action}</div>
      </div>
      {visibleError && <div role="alert" className="mx-6 mb-6 rounded-xl border border-[#ebcfd3] bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)] sm:mx-8">{visibleError}</div>}
    </section>
  </div>
}

export function SuccessPage({ eyebrow, title, description, onBack, actions, children }: { eyebrow: string; title: string; description?: string; onBack: () => void; actions: ReactNode; children?: ReactNode }) {
  return <div className="page max-w-[920px]"><PageHeader eyebrow={eyebrow} title={title} description={description} onBack={onBack} actions={actions} />{children && <div className="rounded-2xl border border-[var(--border)] bg-white p-7">{children}</div>}</div>
}

export function FormGrid({ children }: { children: ReactNode }) {
  return <div className="grid items-start gap-4 sm:grid-cols-2">{children}</div>
}

export function Field({ label, hint, children, wide = false }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return <label className={wide ? "block sm:col-span-2" : "block"}><span className="mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]">{label}</span>{children}{hint && <span className="mt-1.5 block text-[9px] leading-4 text-[var(--ink-muted)]">{hint}</span>}</label>
}

type SelectSize = "sm" | "md"
type SelectWidth = "compact" | "full"
type SelectVariant = "field" | "filter"

export function ResourceSelect({ label, value, onChange, addLabel, onAdd, children, className, wide = false, hideLabel = false, size = "md", width = "full" }: { label: string; value: string; onChange: (value: string) => void; addLabel: string; onAdd: () => void; children: ReactNode; className?: string; wide?: boolean; hideLabel?: boolean; size?: SelectSize; width?: SelectWidth }) {
  return <label className={wide ? "block sm:col-span-2" : "block"}>
    <span className={hideLabel ? "sr-only" : "mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]"}>{label}</span>
    <div className={cn("relative", width === "compact" && "w-52 max-w-full")}>
      <select className={cn(size === "sm" ? compactControl : formControl, "appearance-none pr-10", className)} value={value} onChange={(event) => {
        if (event.target.value === "__add_resource__") onAdd()
        else onChange(event.target.value)
      }}>
        {children}
        <option value="__add_resource__">{addLabel}…</option>
      </select>
      <ChevronDown className="pointer-events-none absolute right-3.5 top-1/2 size-3.5 -translate-y-1/2 text-[var(--ink-muted)]" />
    </div>
  </label>
}

export function SelectControl({ children, className, size = "md", width = "full", variant = "field", ...props }: Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> & { size?: SelectSize; width?: SelectWidth; variant?: SelectVariant }) {
  const controlClass = variant === "filter"
    ? "focus-ring h-10 w-full min-w-0 appearance-none rounded-xl border border-[var(--border)] bg-[var(--surface-soft)] px-3 pr-8 text-[11px] font-medium text-[var(--ink)]"
    : cn(size === "sm" ? compactControl : formControl, "appearance-none pr-10")
  return <div className={cn("relative", width === "compact" && "w-52 max-w-full")}>
    <select className={cn(controlClass, className)} {...props}>{children}</select>
    <ChevronDown className={`pointer-events-none absolute top-1/2 size-3.5 -translate-y-1/2 text-[var(--ink-muted)] ${variant === "filter" ? "right-3" : "right-3.5"}`} />
  </div>
}

export function MenuSelect({ value, onChange, options, placeholder, disabled }: {
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  placeholder: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", close)
    document.addEventListener("keydown", escape)
    return () => {
      document.removeEventListener("mousedown", close)
      document.removeEventListener("keydown", escape)
    }
  }, [open])
  const selected = options.find((item) => item.value === value)
  return <div ref={root} className="relative">
    <button type="button" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
      className={cn(formControl, "flex items-center justify-between gap-3 text-left disabled:opacity-60")}
      onClick={() => setOpen((current) => !current)}>
      <span className={cn("truncate", !selected && "text-[var(--ink-muted)]")}>{selected?.label ?? placeholder}</span>
      <ChevronDown className="size-3.5 shrink-0 text-[var(--ink-muted)]" />
    </button>
    {open && <div role="listbox" className="absolute z-30 mt-1 max-h-60 w-full overflow-y-auto rounded-xl border border-[var(--border)] bg-white p-1 shadow-xl">
      {options.length ? options.map((item) => <button key={item.value} type="button" role="option" aria-selected={item.value === value}
        className={cn("block w-full truncate rounded-lg px-3 py-2 text-left text-[11px]", item.value === value ? "bg-[var(--surface-soft)] font-semibold" : "hover:bg-[var(--surface-soft)]")}
        onClick={() => { onChange(item.value); setOpen(false) }}>{item.label}</button>)
        : <p className="px-3 py-2 text-[11px] text-[var(--ink-muted)]">{placeholder}</p>}
    </div>}
  </div>
}

export function Fieldset({ label, hint, children, wide = false }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return <fieldset className={wide ? "sm:col-span-2" : undefined}><legend className="mb-1.5 text-[10px] font-semibold text-[var(--ink-soft)]">{label}</legend>{children}{hint && <span className="mt-1.5 block text-[9px] leading-4 text-[var(--ink-muted)]">{hint}</span>}</fieldset>
}

export const formControl = "focus-ring block h-11 w-full min-w-0 rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none placeholder:text-[var(--ink-muted)]"
const compactControl = cn(formControl, "h-9 rounded-lg")
