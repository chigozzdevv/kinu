import { ChevronDown } from "lucide-react"
import { useContext, useEffect, useId, useRef, useState } from "react"
import { DashboardLinkNavigation } from "../lib/dashboard-link-navigation"
import { breadcrumbGroups, dashboardPath, type NavItem } from "../lib/navigation"

type DashboardPage = { id: NavItem; label: string }

function DashboardPageLink({ page, className, onFollow, onNavigate }: { page: DashboardPage; className: string; onFollow?: () => void; onNavigate?: () => void }) {
  const navigate = useContext(DashboardLinkNavigation)
  return <a href={dashboardPath({ section: page.id })} className={className} onClick={(event) => {
    if ((!navigate && !onNavigate) || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    onFollow?.()
    if (onNavigate) onNavigate()
    else navigate?.(page.id)
    window.scrollTo({ top: 0, behavior: "instant" })
  }}>{page.label}</a>
}

const crumbClass = "focus-ring inline-flex min-h-7 shrink-0 items-center rounded text-[var(--ink-soft)] underline-offset-2 hover:text-[var(--ink)] hover:underline focus-visible:underline"

export function DashboardBreadcrumb({ group, parent, onParentNavigate }: { group: string; parent?: string; onParentNavigate?: () => void }) {
  const [open, setOpen] = useState(false)
  const disclosure = useRef<HTMLDivElement>(null)
  const toggle = useRef<HTMLButtonElement>(null)
  const linksId = useId()
  const pages = breadcrumbGroups.find((item) => item.label === group)?.pages
  const parentPage = pages?.find((page) => page.label === parent)

  useEffect(() => {
    if (!open) return
    const dismiss = (event: PointerEvent) => {
      if (!disclosure.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("pointerdown", dismiss)
    return () => document.removeEventListener("pointerdown", dismiss)
  }, [open])

  if (!pages) return <span>{[group, parent].filter(Boolean).join(" / ")}</span>

  return <>
    <div ref={disclosure} className="relative shrink-0" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
    }} onKeyDown={(event) => {
      if (event.key !== "Escape" || !open) return
      event.preventDefault()
      setOpen(false)
      toggle.current?.focus()
    }}>
      <button type="button" ref={toggle} aria-expanded={open} aria-controls={open ? linksId : undefined} onClick={() => setOpen(!open)} className={`${crumbClass} gap-0.5`}>
        {group} <ChevronDown aria-hidden="true" className={`size-3 ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <ul id={linksId} aria-label={`${group} pages`} className="absolute left-0 top-full z-40 mt-1 w-36 rounded-xl border border-[var(--border)] bg-white p-1 shadow-lg">
        {pages.map((page) => <li key={page.id}><DashboardPageLink page={page} onNavigate={page.label === parent ? onParentNavigate : undefined} onFollow={() => setOpen(false)} className="focus-ring block rounded-lg px-3 py-2 text-[12px] text-[var(--ink-soft)] hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" /></li>)}
      </ul>}
    </div>
    {parentPage && <><span aria-hidden="true">/</span><DashboardPageLink page={parentPage} onNavigate={onParentNavigate} className={crumbClass} onFollow={() => setOpen(false)} /></>}
  </>
}
