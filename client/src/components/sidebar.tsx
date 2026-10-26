import { useEffect, useRef, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ChevronDown,
  House,
  KeyRound,
  LogOut,
  Menu,
  ScrollText,
  SlidersHorizontal,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react"
import kinuLogo from "../assets/kinu-logo.svg"
import { api } from "../lib/api"
import type { NavItem } from "../lib/navigation"

interface NavEntry {
  id: NavItem
  label: string
  count?: number
}

interface NavGroup {
  id: "inventory" | "operations"
  label: string
  icon: LucideIcon
  items: NavEntry[]
}

const groups: NavGroup[] = [
  {
    id: "inventory",
    label: "Inventory",
    icon: KeyRound,
    items: [
      { id: "credentials", label: "Credentials" },
      { id: "connections", label: "Connections" },
      { id: "playbooks", label: "Playbooks" },
    ],
  },
  {
    id: "operations",
    label: "Operations",
    icon: Workflow,
    items: [
      { id: "incidents", label: "Incidents" },
      { id: "rotations", label: "Rotations" },
      { id: "approvals", label: "Approvals" },
    ],
  },
]

function Brand() {
  return (
    <div className="flex h-10 items-center px-3">
      <img src={kinuLogo} alt="Kinu" className="h-auto w-[112px] object-contain object-left" />
    </div>
  )
}

function SidebarContent({ currentNav, onNavigate, onLogout }: { currentNav: NavItem; onNavigate: (item: NavItem) => void; onLogout: () => void }) {
  const summary = useQuery({ queryKey: ["overview"], queryFn: () => api.getOverview() })
  const profile = useQuery({ queryKey: ["profile"], queryFn: () => api.getProfile() })
  const [expanded, setExpanded] = useState<Record<NavGroup["id"], boolean>>({
    inventory: true,
    operations: true,
  })
  const [accountOpen, setAccountOpen] = useState(false)
  const accountMenu = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!accountOpen) return
    const close = (event: PointerEvent) => {
      if (!accountMenu.current?.contains(event.target as Node)) setAccountOpen(false)
    }
    window.addEventListener("pointerdown", close)
    return () => window.removeEventListener("pointerdown", close)
  }, [accountOpen])

  return (
    <>
      <div className="px-4 pt-5">
        <Brand />
      </div>

      <nav className="mt-9 flex-1 overflow-y-auto px-4 pb-5">
        <PrimaryItem icon={House} label="Overview" active={currentNav === "overview"} onClick={() => onNavigate("overview")} />

        <div className="mt-4 space-y-3">
          {groups.map((group) => {
            const open = expanded[group.id]
            const groupActive = group.items.some((item) => item.id === currentNav)

            return (
              <div key={group.id}>
                <button
                  className={`focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-[13px] font-medium transition hover:bg-white/35 ${
                    groupActive ? "text-[var(--ink)]" : "text-[var(--ink-soft)]"
                  }`}
                  aria-expanded={open}
                  onClick={() => setExpanded((current) => ({ ...current, [group.id]: !current[group.id] }))}
                  onPointerUp={(event) => event.currentTarget.blur()}
                >
                  <group.icon className="size-[17px] text-[var(--accent)]" strokeWidth={1.8} />
                  <span className="flex-1">{group.label}</span>
                  <ChevronDown className={`size-3.5 text-[var(--accent)] transition-transform ${open ? "" : "-rotate-90"}`} strokeWidth={2} />
                </button>

                {open && (
                  <div className="nav-children mt-0.5 space-y-0.5">
                    {group.items.map((item) => {
                      const active = item.id === currentNav
                      const count = item.id === "approvals" ? summary.data?.pending_approvals : item.count

                      return (
                        <button
                          key={item.id}
                          className={`focus-ring flex w-full items-center rounded-lg px-3 py-2 text-left text-[13px] font-medium transition ${
                            active
                              ? "bg-[var(--surface-active)] text-[var(--ink)]"
                              : "text-[var(--ink-soft)] hover:bg-white/45 hover:text-[var(--ink)]"
                          }`}
                          onClick={() => onNavigate(item.id)}
                          onPointerUp={(event) => event.currentTarget.blur()}
                        >
                          <span className="flex-1">{item.label}</span>
                          {count ? (
                            <span className="grid min-w-5 place-items-center rounded-lg bg-white px-1.5 py-0.5 text-[10px] font-semibold text-[var(--ink-soft)]">
                              {count}
                            </span>
                          ) : null}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div className="mt-4">
          <PrimaryItem icon={ScrollText} label="Audits" active={currentNav === "audits"} onClick={() => onNavigate("audits")} />
        </div>
      </nav>

      <div className="px-4 pb-5">
        <div ref={accountMenu} className="relative">
          {accountOpen && <div role="menu" className="absolute bottom-[calc(100%+8px)] left-0 right-0 overflow-hidden rounded-xl border border-[var(--border)] bg-white p-1.5 shadow-[0_14px_32px_rgba(24,26,29,0.12)]">
            <button role="menuitem" className="focus-ring flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[11px] font-medium text-[var(--ink-soft)] hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" onClick={() => { setAccountOpen(false); onNavigate("settings") }}><SlidersHorizontal className="size-4" strokeWidth={1.8} />Settings</button>
            <button role="menuitem" className="focus-ring flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[11px] font-medium text-[var(--ink-soft)] hover:bg-[var(--surface-soft)] hover:text-[var(--ink)]" onClick={() => { setAccountOpen(false); onLogout() }}><LogOut className="size-4" strokeWidth={1.8} />Log out</button>
          </div>}
          <button className={`focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition hover:bg-white/35 ${currentNav === "settings" ? "bg-[var(--surface-active)]" : ""}`} aria-expanded={accountOpen} aria-haspopup="menu" onClick={() => setAccountOpen((open) => !open)}>
            <span className="grid size-8 place-items-center rounded-lg bg-white text-[10px] font-semibold text-[var(--accent)]">{initials(profile.data?.display_name ?? "User")}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-[var(--ink)]">{profile.data?.display_name ?? "Account"}</span>
            <ChevronDown className={`size-3.5 text-[var(--accent)] transition-transform ${accountOpen ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>
    </>
  )
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "U"
}

function PrimaryItem({ icon: Icon, label, active, onClick }: { icon: LucideIcon; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      className={`focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-[13px] font-medium transition ${
        active ? "bg-[var(--surface-active)] text-[var(--ink)]" : "text-[var(--ink-soft)] hover:bg-white/45 hover:text-[var(--ink)]"
      }`}
      onClick={onClick}
      onPointerUp={(event) => event.currentTarget.blur()}
    >
      <Icon className="size-[17px] text-[var(--accent)]" strokeWidth={1.8} />
      <span className="flex-1">{label}</span>
    </button>
  )
}

export function Shell({ currentNav, onNavigate, onLogout, children }: { currentNav: NavItem; onNavigate: (item: NavItem) => void; onLogout: () => void; children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [currentNav])

  return (
    <div className="app-shell">
      <aside className="sticky top-6 hidden h-[calc(100vh-48px)] w-[240px] shrink-0 flex-col overflow-hidden rounded-2xl bg-[var(--sidebar)] lg:flex">
        <SidebarContent currentNav={currentNav} onNavigate={onNavigate} onLogout={onLogout} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-x-hidden">
        <header className="flex h-14 items-center justify-between bg-[var(--workspace)] px-6 lg:hidden">
          <Brand />
          <button aria-label="Open navigation" className="focus-ring rounded-lg p-2 text-[var(--ink-soft)] hover:bg-white/50" onClick={() => setMobileOpen(true)}>
            <Menu className="size-5" />
          </button>
        </header>

        <main className="flex-1">
          {children}
        </main>
      </div>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <div className="relative z-10 flex w-[280px] flex-col bg-[var(--sidebar)]">
            <div className="flex h-14 items-center justify-end px-4">
              <button aria-label="Close navigation" className="focus-ring rounded-lg p-2 text-[var(--ink-soft)] hover:bg-white/50" onClick={() => setMobileOpen(false)}>
                <X className="size-5" />
              </button>
            </div>
            <SidebarContent currentNav={currentNav} onNavigate={(item) => { onNavigate(item); setMobileOpen(false) }} onLogout={onLogout} />
          </div>
        </div>
      )}
    </div>
  )
}
