import { AlertCircle, LoaderCircle } from "lucide-react"

export function Loading() {
  return <div className="grid min-h-52 place-items-center"><LoaderCircle className="size-5 animate-spin text-[var(--accent)]" /></div>
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
