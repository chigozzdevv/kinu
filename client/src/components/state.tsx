import { AlertCircle, LoaderCircle } from "lucide-react"
import type { ReactNode } from "react"

export function Notice({ children }: { children: ReactNode }) {
  return <div role="status" className="mb-5 rounded-xl border border-[var(--border-soft)] p-3 text-xs text-[var(--ink-soft)]">{children}</div>
}

export function Loading() {
  return <div className="grid min-h-52 place-items-center"><LoaderCircle className="size-5 animate-spin text-[var(--accent)]" /></div>
}

export function RetryNotice({ children, onRetry }: { children: ReactNode; onRetry: () => void }) {
  return <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-[var(--ink-soft)]">
    {children}
    <button type="button" className="focus-ring rounded text-[var(--ink)] underline" onClick={onRetry}>Retry</button>
  </div>
}

export function Failure({ error }: { error: Error }) {
  return (
    <div className="panel flex min-h-52 flex-col items-center justify-center px-6 text-center">
      <AlertCircle className="mb-3 size-5 text-[var(--red)]" />
      <div className="text-sm font-semibold">Could not load this view</div>
      <div className="mt-1 max-w-md text-xs text-[var(--ink-soft)]">{error.message}</div>
    </div>
  )
}
