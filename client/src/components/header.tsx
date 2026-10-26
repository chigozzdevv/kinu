import { ArrowLeft, Check, ChevronDown } from "lucide-react"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { useOrganisation } from "../lib/useorganisation"

type PageHeaderProps = {
  section?: string
  eyebrow?: string
  title?: string
  titlePrefix?: ReactNode
  description?: string
  actions?: ReactNode
  onBack?: () => void
}

export function PageHeader({ section, eyebrow, title, titlePrefix, description, actions, onBack }: PageHeaderProps) {
  const { active, memberships, select } = useOrganisation()
  const [organisationsOpen, setOrganisationsOpen] = useState(false)
  const organisationMenu = useRef<HTMLDivElement>(null)
  const parts = section?.split(" · ") ?? []
  const resolvedTitle = title ?? parts.at(-1) ?? ""
  const resolvedEyebrow = eyebrow ?? (parts.length > 1 ? parts.slice(0, -1).join(" / ") : undefined)

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!organisationMenu.current?.contains(event.target as Node)) setOrganisationsOpen(false)
    }
    window.addEventListener("pointerdown", close)
    return () => window.removeEventListener("pointerdown", close)
  }, [])

  return (
    <header className="mb-8 flex flex-col items-start justify-between gap-6 sm:flex-row">
      <div className="min-w-0">
        <div className="mb-4 flex items-center gap-2 text-[10px] font-medium text-[var(--ink-muted)]">
          {onBack && <button className="focus-ring -ml-2 grid size-7 place-items-center rounded-lg transition hover:bg-white hover:text-[var(--ink)]" aria-label="Back" onClick={onBack}><ArrowLeft className="size-3.5" /></button>}
          <div ref={organisationMenu} className="relative">
          <button
            className="focus-ring flex items-center gap-1 rounded-lg px-2 py-1.5 transition hover:bg-white hover:text-[var(--ink)]"
            aria-expanded={organisationsOpen}
            aria-haspopup="menu"
            onClick={() => setOrganisationsOpen((open) => !open)}
          >
            <span>{active.organisation.name}</span>
            <ChevronDown className={`size-3 transition-transform ${organisationsOpen ? "rotate-180" : ""}`} />
          </button>
          {organisationsOpen && (
            <div className="absolute left-0 top-9 z-40 w-56 rounded-xl border border-[var(--border)] bg-white p-2 shadow-[0_16px_45px_rgba(25,27,30,0.12)]" role="menu">
              <div className="px-3 pb-2 pt-1 text-[9px] font-semibold uppercase tracking-[0.08em] text-[var(--ink-muted)]">Organization</div>
              {memberships.map((membership) => {
                const selected = membership.organisation.id === active.organisation.id
                return <button key={membership.organisation.id} className={`focus-ring flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left text-[11px] font-semibold text-[var(--ink)] ${selected ? "bg-[var(--surface-soft)]" : "hover:bg-[var(--surface-soft)]"}`} role="menuitem" onClick={() => { setOrganisationsOpen(false); select(membership.organisation.id) }}>
                  {membership.organisation.name}
                  {selected && <Check className="size-3.5 text-[var(--green)]" />}
                </button>
              })}
            </div>
          )}
          </div>
          {resolvedEyebrow && <><span>/</span><span>{resolvedEyebrow}</span></>}
        </div>
        <h1 className="m-0 flex items-center gap-3 text-[28px] font-semibold tracking-[-0.045em] text-[var(--ink)]">{titlePrefix && <span className="shrink-0">{titlePrefix}</span>}{resolvedTitle}</h1>
        {description && <p className="mt-2 max-w-2xl text-[12px] leading-5 text-[var(--ink-soft)]">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2 pt-0 sm:pt-9">{actions}</div>}
    </header>
  )
}
