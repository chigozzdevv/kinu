import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronRight, ScrollText } from "lucide-react"
import { Detail, DetailCard, DetailList } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { AuditEvent } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

const actorName = (value: string) => titleCase(value.replace(/^actor_/, ""))
const resourceName = (value: string) => titleCase(value.split("/").at(-1) ?? value)

export function AuditsPage() {
  const [search, setSearch] = useState("")
  const [kind, setKind] = useState("all")
  const [selected, setSelected] = useState<AuditEvent | null>(null)
  const query = useQuery({ queryKey: ["audits"], queryFn: () => api.getAudits() })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (query.data ?? []).filter((item) => (kind === "all" || item.kind === kind) && (!term || `${item.kind} ${item.actor_id} ${item.resource} ${item.run_id}`.toLowerCase().includes(term)))
  }, [kind, query.data, search])
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  if (selected) return <div className="page"><PageHeader eyebrow="System / Audit" title={titleCase(selected.kind)} onBack={() => setSelected(null)} /><DetailCard><DetailList><Detail label="Sequence">#{selected.sequence}</Detail><Detail label="Actor">{actorName(selected.actor_id)}</Detail><Detail label="Resource">{resourceName(selected.resource)}</Detail><Detail label="Occurred">{formatDate(selected.occurred_at, true)}</Detail><Detail label="Region">{selected.region}</Detail><Detail label="Status"><Badge variant="healthy">Verified</Badge></Detail></DetailList></DetailCard></div>

  return (
    <div className="page">
      <PageHeader eyebrow="System" title="Audit" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search audit events" onClear={() => { setSearch(""); setKind("all") }} filters={[{ label: "Event", value: kind, defaultValue: "all", onChange: (event) => setKind(event.target.value), children: <><option value="all">All events</option>{[...new Set(query.data!.map((item) => item.kind))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
      <Table><TableHeader><TableRow><TableHead>Event</TableHead><TableHead>Time</TableHead><TableHead>Actor</TableHead><TableHead>Resource</TableHead><TableHead>Sequence</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((event) => <TableRow key={event.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={() => setSelected(event)}><Marker icon={ScrollText} />{titleCase(event.kind)}</button></TableCell><TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(event.occurred_at, true)}</TableCell><TableCell>{actorName(event.actor_id)}</TableCell><TableCell className="max-w-[260px] truncate text-[10px]">{resourceName(event.resource)}</TableCell><TableCell className="text-[10px] text-[var(--ink-muted)]">#{event.sequence}</TableCell><TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={() => setSelected(event)}>View event <ChevronRight className="size-3.5" /></Button></div></TableCell></TableRow>)}</TableBody></Table>
    </div>
  )
}
