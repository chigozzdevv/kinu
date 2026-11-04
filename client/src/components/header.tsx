import { ArrowLeft, Check, ChevronDown } from "lucide-react"
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { useOrganisation } from "../lib/useorganisation"
import { DashboardBreadcrumb } from "./dashboard-breadcrumb"

type PageHeaderProps = {
  section?: string
  eyebrow?: string
  title?: string
  titlePrefix?: ReactNode
  titleInBreadcrumb?: boolean
  description?: string
  actions?: ReactNode
  onBack?: () => void
}

const SectionContext = createContext<string | null>(null)

export function PageSection({ name, children }: { name: string; children: ReactNode }) {
  return <SectionContext value={name}>{children}</SectionContext>
}

export function PageHeader({ section, eyebrow, title, titlePrefix, titleInBreadcrumb = false, description, actions, onBack }: PageHeaderProps) {
  const { active, memberships, select } = useOrganisation()
  const [organisationsOpen, setOrganisationsOpen] = useState(false)
  const organisationMenu = useRef<HTMLDivElement>(null)
  const parts = section?.split(" · ") ?? []
  const resolvedTitle = title ?? parts.at(-1) ?? ""
  const resolvedEyebrow = useContext(SectionContext) ?? eyebrow ?? (parts.length > 1 ? parts.slice(0, -1).join(" / ") : undefined)
  const [group, parent] = resolvedEyebrow?.split(" / ") ?? []

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!organisationMenu.current?.contains(event.target as Node)) setOrganisationsOpen(false)
    }
    window.addEventListener("pointerdown", close)
    return () => window.removeEventListener("pointerdown", close)
  }, [])

  const heading = <h1 aria-current={titleInBreadcrumb ? "page" : undefined} className={`m-0 flex items-center font-semibold text-[var(--ink)] ${titleInBreadcrumb ? "min-w-0 gap-1 text-[12px]" : "gap-3 text-[28px] tracking-[-0.045em]"}`}>
    {titlePrefix && <span className={titleInBreadcrumb ? "shrink-0 max-[380px]:sr-only" : "shrink-0"}>{titlePrefix}</span>}
    {titleInBreadcrumb ? <span className="truncate" title={resolvedTitle}>{resolvedTitle}</span> : resolvedTitle}
  </h1>

  return (
    <header className={`flex flex-wrap justify-between gap-x-6 ${titleInBreadcrumb ? "mb-3 items-center gap-y-2" : "mb-8 items-start gap-y-4"}`}>
      <div className="min-w-0 flex-1 basis-64">
        <nav aria-label="Breadcrumb" className={`flex items-center font-medium text-[var(--ink-muted)] ${titleInBreadcrumb ? "gap-1 text-[11px] sm:gap-2" : "mb-4 gap-2 text-[10px]"}`}>
          {onBack && <button className="focus-ring -ml-2 grid size-7 shrink-0 place-items-center rounded-lg transition hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" aria-label="Back" onClick={onBack}><ArrowLeft className="size-3.5" /></button>}
          <div ref={organisationMenu} className={`relative ${titleInBreadcrumb ? "max-w-[26%]" : ""}`}>
          <button
            className="focus-ring flex max-w-full items-center gap-1 rounded-lg px-2 py-1.5 transition hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]"
            aria-expanded={organisationsOpen}
            aria-haspopup="menu"
            onClick={() => setOrganisationsOpen((open) => !open)}
          >
            <span className={titleInBreadcrumb ? "truncate" : undefined}>{active.organisation.name}</span>
            <ChevronDown className={`size-3 shrink-0 transition-transform ${organisationsOpen ? "rotate-180" : ""}`} />
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
          {resolvedEyebrow && <><span aria-hidden="true">/</span><DashboardBreadcrumb key={resolvedEyebrow} group={group} parent={parent} onParentNavigate={onBack} /></>}
          {titleInBreadcrumb && <><span aria-hidden="true">/</span>{heading}</>}
        </nav>
        {!titleInBreadcrumb && heading}
        {description && <p className="mt-2 max-w-2xl text-[12px] leading-5 text-[var(--ink-soft)]">{description}</p>}
      </div>
      {actions && <div className={`flex max-w-full flex-wrap items-center gap-2 ${titleInBreadcrumb ? "empty:hidden" : "sm:pt-9"}`}>{actions}</div>}
    </header>
  )
}
