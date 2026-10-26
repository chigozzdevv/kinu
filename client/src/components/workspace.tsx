import { ChevronDown, X } from "lucide-react"
import { useEffect, useState, type ReactNode, type SelectHTMLAttributes } from "react"
import { PageHeader } from "./header"
import { Button } from "./ui/button"

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
        <div className="mx-auto flex w-full max-w-[760px] items-center justify-between px-6 pt-6 sm:px-8">
          <span className="text-[11px] font-semibold text-[var(--ink)]">{steps[current]}</span>
          <span className="text-[10px] font-medium text-[var(--ink-muted)]">Step {current + 1} of {steps.length}</span>
        </div>
        <div className="mx-auto min-h-[360px] w-full max-w-[760px] px-6 py-7 sm:px-8">{children}</div>
        {visibleError && <div role="alert" className="mx-6 mb-5 rounded-xl border border-[#ebcfd3] bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)] sm:mx-8">{visibleError}</div>}
        <footer className="flex items-center gap-3 bg-white px-6 py-4 sm:px-8">
          <div className="flex-1">{current > 0 && <Button variant="ghost" onClick={onBack}>Back</Button>}</div>
          <Button variant="secondary" onClick={onCancel}>Cancel</Button>
          {primary}
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
      actions={<button className="focus-ring grid size-8 place-items-center rounded-lg text-[var(--ink-muted)] transition hover:bg-white hover:text-[var(--ink)]" aria-label="Close" onClick={onClose}><X className="size-4" /></button>}
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

export function ResourceSelect({ label, value, onChange, addLabel, onAdd, children, className = formControl, wide = false }: { label: string; value: string; onChange: (value: string) => void; addLabel: string; onAdd: () => void; children: ReactNode; className?: string; wide?: boolean }) {
  return <label className={wide ? "block sm:col-span-2" : "block"}>
    <span className="mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]">{label}</span>
    <div className="relative">
      <select className={`${className} appearance-none pr-10`} value={value} onChange={(event) => {
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

export function SelectControl({ children, className = formControl, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <div className="relative">
    <select className={`${className} appearance-none pr-10`} {...props}>{children}</select>
    <ChevronDown className="pointer-events-none absolute right-3.5 top-1/2 size-3.5 -translate-y-1/2 text-[var(--ink-muted)]" />
  </div>
}

export function Fieldset({ label, hint, children, wide = false }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return <fieldset className={wide ? "sm:col-span-2" : undefined}><legend className="mb-1.5 text-[10px] font-semibold text-[var(--ink-soft)]">{label}</legend>{children}{hint && <span className="mt-1.5 block text-[9px] leading-4 text-[var(--ink-muted)]">{hint}</span>}</fieldset>
}

export const formControl = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none placeholder:text-[var(--ink-muted)]"
