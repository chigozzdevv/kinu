import { X } from "lucide-react"
import { useEffect, useId, useRef, type ReactNode } from "react"
import { Button } from "./button"

const focusable = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"

type ModalProps = {
  isOpen: boolean
  onClose: () => void
  title: string
  description?: string
  subtitle?: string
  children?: ReactNode
  actions?: ReactNode
  footerStart?: ReactNode
  size?: "narrow" | "compact" | "wide"
  cancelLabel?: string | false
  showClose?: boolean
}

export function Modal({ isOpen, onClose, title, description, subtitle, children, actions, footerStart, size = "compact", cancelLabel = "Cancel", showClose = true }: ModalProps) {
  const titleId = useId()
  const panel = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!isOpen) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    panel.current?.focus()

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== "Tab" || !panel.current) return
      const elements = [...panel.current.querySelectorAll<HTMLElement>(focusable)]
      if (!elements.length) return
      const first = elements[0]
      const last = elements.at(-1)!
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    window.addEventListener("keydown", handleKey)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener("keydown", handleKey)
      previousFocus?.focus()
    }
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4 sm:p-6">
      <div className="absolute inset-0 bg-black/20 backdrop-blur-[2px]" onMouseDown={onClose} aria-hidden="true" />
      <section ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} className={`relative z-10 flex max-h-[calc(100vh-48px)] w-full flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-white shadow-[0_24px_70px_rgba(25,27,30,0.16)] outline-none ${size === "wide" ? "max-w-[680px]" : size === "narrow" ? "max-w-[420px]" : "max-w-[520px]"}`}>
        <header className="flex items-start gap-4 px-6 py-5">
          <div className="min-w-0">
            <h2 id={titleId} className="m-0 text-lg font-semibold tracking-[-0.035em] text-[var(--ink)]">{title}</h2>
            {description && <p className="mt-2.5 max-w-[42ch] text-[10px] leading-4 text-[var(--ink-soft)]">{description}</p>}
            {subtitle && <div className="mono mt-1 truncate text-[10px] text-[var(--ink-muted)]">{subtitle}</div>}
          </div>
          {showClose && <button className="focus-ring ml-auto grid size-8 shrink-0 place-items-center rounded-lg text-[var(--ink-muted)] transition hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" aria-label="Close" onClick={onClose}><X className="size-4" /></button>}
        </header>
        {children !== undefined && children !== null && <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">{children}</div>}
        {(actions || footerStart) && <footer className="flex shrink-0 items-center gap-3 bg-white px-6 py-4">
          <div className="min-w-0 flex-1">{footerStart}</div>
          {cancelLabel && <Button variant="secondary" onClick={onClose}>{cancelLabel}</Button>}
          {actions}
        </footer>}
      </section>
    </div>
  )
}
