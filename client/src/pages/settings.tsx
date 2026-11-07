import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { AiSettings } from "../components/ai"
import { DetailTabs } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading, Notice } from "../components/state"
import { SettingsFooter, SettingsRow } from "../components/settings"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, Fieldset, SelectControl, formControl } from "../components/workspace"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"
import { settingsTabs, type SettingsTab } from "../lib/navigation"
import { cn } from "../lib/utils"
import type { EmailNotificationEndpoint, MemberRole, NotificationDelivery, NotificationTopic, TeamMember } from "../types"

type Tab = "profile" | "team" | "notifications" | "ai"

const roles: Array<{ value: MemberRole; label: string }> = [
  { value: "viewer", label: "Viewer" },
  { value: "operator", label: "Operator" },
  { value: "administrator", label: "Administrator" },
]

function identifier() {
  return `endpoint_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function topicLabels(endpoint: EmailNotificationEndpoint, topics: NotificationTopic[]) {
  const current = new Set(endpoint.event_kinds)
  return topics
    .filter((topic) => topic.event_kinds.every((kind) => current.has(kind)))
    .map((topic) => topic.label)
    .join(", ")
}

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>(() => {
    const requested = new URLSearchParams(window.location.search).get("tab")
    return requested && settingsTabs.includes(requested as SettingsTab) ? requested as Tab : "profile"
  })
  const [inviting, setInviting] = useState(false)
  const [editingMember, setEditingMember] = useState<TeamMember | null>(null)
  const [addingEmail, setAddingEmail] = useState(false)
  const profile = useQuery({ queryKey: ["profile"], queryFn: () => api.getProfile() })
  const administrator = profile.data?.role === "administrator"
  const team = useQuery({ queryKey: ["team"], queryFn: () => api.getTeam(), enabled: Boolean(administrator && tab === "team") })
  const delivery = useQuery({ queryKey: ["notification-settings"], queryFn: () => api.getNotificationSettings(), enabled: tab === "notifications" })
  const endpoints = useQuery({ queryKey: ["notification-endpoints"], queryFn: () => api.getNotificationEndpoints(), enabled: tab === "notifications" })
  const topics = useQuery({ queryKey: ["notification-topics"], queryFn: () => api.getNotificationTopics(), enabled: tab === "notifications" })
  const deliveries = useQuery({ queryKey: ["notification-deliveries"], queryFn: () => api.getNotificationDeliveries(), enabled: tab === "notifications" })
  useEffect(() => {
    if (profile.data && !administrator && tab === "team") setTab("profile")
  }, [administrator, profile.data, tab])
  const tabs = administrator
    ? [{ id: "profile" as const, label: "Profile" }, { id: "team" as const, label: "Team" }, { id: "notifications" as const, label: "Notifications" }, { id: "ai" as const, label: "AI" }]
    : [{ id: "profile" as const, label: "Profile" }, { id: "notifications" as const, label: "Notifications" }, { id: "ai" as const, label: "AI" }]
  const actions = tab === "team"
    ? <Button size="sm" onClick={() => setInviting(true)}><Plus className="size-3.5" /> Invite member</Button>
    : tab === "notifications"
      ? <Button size="sm" disabled={!delivery.data?.email_enabled} onClick={() => setAddingEmail(true)}><Plus className="size-3.5" /> Add email</Button>
      : undefined

  const activeLoading = profile.isLoading || (tab === "team" && team.isLoading) || (tab === "notifications" && (delivery.isLoading || endpoints.isLoading || topics.isLoading))
  if (activeLoading) return <div className="page"><Loading /></div>
  const error = profile.error ?? (tab === "team" ? team.error : null) ?? (tab === "notifications" ? delivery.error ?? endpoints.error ?? topics.error : null)
  if (error) return <div className="page"><Failure error={error} /></div>

  return <div className="page">
    <div className="max-w-[640px]">
      <PageHeader eyebrow="System" title="Settings" />
      <DetailTabs items={tabs} value={tab} onChange={(next) => { setTab(next); window.history.replaceState({}, "", `/dashboard/settings${next === "profile" ? "" : `?tab=${next}`}`) }} className="mb-0" />
      <div className="px-4 pb-4 pt-2">
        {actions && <div className="flex justify-end py-4">{actions}</div>}
        {tab === "profile" && <ProfileSettings profile={profile.data!} />}
        {tab === "ai" && <AiSettings administrator={administrator} />}
        {tab === "team" && <TeamSettings members={team.data!} currentId={profile.data!.id} onEdit={setEditingMember} />}
        {tab === "notifications" && <>
          {!delivery.data?.email_enabled && <Notice>Email notifications are not configured — alerts appear in Kinu only.</Notice>}
          <EmailSettings endpoints={endpoints.data!} topics={topics.data!} />
          {deliveries.data && deliveries.data.length > 0 && <DeliveryProblems problems={deliveries.data} endpoints={endpoints.data!} />}
          {!deliveries.data && deliveries.error && <div role="alert" className="mt-8 text-[10px] text-[var(--red)]">Delivery status is unavailable right now.</div>}
        </>}
      </div>
    </div>
    {inviting && <InviteMemberModal onClose={() => setInviting(false)} />}
    {editingMember && <EditMemberModal key={editingMember.id} member={editingMember} onClose={() => setEditingMember(null)} />}
    {addingEmail && <EmailModal onClose={() => setAddingEmail(false)} defaultEmail={profile.data!.email} topics={topics.data!} />}
  </div>
}

function ProfileSettings({ profile }: { profile: Awaited<ReturnType<typeof api.getProfile>> }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(profile.display_name)
  useEffect(() => setName(profile.display_name), [profile.display_name])
  const save = useMutation({
    mutationFn: () => api.updateProfile(profile.revision, name.trim()),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["profile"] }),
        queryClient.invalidateQueries({ queryKey: ["team"] }),
      ])
    },
  })
  return <div>
    <dl className="divide-y divide-[var(--border-soft)]">
      <SettingsRow label="Name" htmlFor="profile-name"><input id="profile-name" className={cn(formControl, "h-9 w-64 max-w-full rounded-lg")} value={name} onChange={(event) => { setName(event.target.value); save.reset() }} /></SettingsRow>
      <SettingsRow label="Email">{profile.email}</SettingsRow>
      <SettingsRow label="Connected via">{profile.connected_via}</SettingsRow>
    </dl>
    <SettingsFooter>
      {save.error && <div role="alert" className="mb-4 text-[10px] text-[var(--red)]">{save.error.message}</div>}
      <Button size="sm" disabled={!name.trim() || name.trim() === profile.display_name || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save changes"}</Button>
    </SettingsFooter>
  </div>
}

function TeamSettings({ members, currentId, onEdit }: { members: TeamMember[]; currentId: string; onEdit: (member: TeamMember) => void }) {
  const queryClient = useQueryClient()
  const cancel = useMutation({
    mutationFn: (member: TeamMember) => api.cancelTeamInvitation(member),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["team"] }),
  })
  return <>
    {cancel.error && <div role="alert" className="mb-4 text-[10px] text-[var(--red)]">{cancel.error.message}</div>}
    <Table responsive="compact" className="min-w-[520px]">
      <TableHeader><TableRow><TableHead>Member</TableHead><TableHead>Role</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody columns={4} emptyMessage="No team members.">{members.map((member) => <TableRow key={member.id}>
        <TableCell primary><div className="font-medium">{member.display_name ?? member.email}</div>{member.display_name && <div className="mt-1 text-[10px] text-[var(--ink-muted)]">{member.email}</div>}</TableCell>
        <TableCell label="Role">{titleCase(member.role)}</TableCell>
        <TableCell label="Status"><Badge variant={member.status === "active" ? "healthy" : "neutral"}>{titleCase(member.status)}</Badge></TableCell>
        <TableCell action className="pr-0">{member.status === "pending" ? <Button variant="ghost" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate(member)}>Cancel</Button> : member.id !== currentId ? <Button variant="ghost" size="sm" onClick={() => onEdit(member)}>Edit</Button> : null}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </>
}

function InviteMemberModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<MemberRole>("viewer")
  const invite = useMutation({
    mutationFn: () => api.inviteTeamMember(email.trim(), role),
    onSuccess: async () => {
      onClose()
      await queryClient.invalidateQueries({ queryKey: ["team"] })
    },
  })
  return <Modal isOpen onClose={onClose} title="Invite member" actions={<Button disabled={!email.trim() || invite.isPending} onClick={() => invite.mutate()}>{invite.isPending ? "Inviting…" : "Invite"}</Button>}>
    <div className="space-y-4">
      <Field label="Email"><input className={formControl} type="email" value={email} onChange={(event) => { setEmail(event.target.value); invite.reset() }} /></Field>
      <Field label="Role"><SelectControl value={role} onChange={(event) => { setRole(event.target.value as MemberRole); invite.reset() }}>{roles.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</SelectControl></Field>
      {invite.error && <div role="alert" className="text-[10px] text-[var(--red)]">{invite.error.message}</div>}
    </div>
  </Modal>
}

function EditMemberModal({ member, onClose }: { member: TeamMember; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [role, setRole] = useState<MemberRole>(member.role)
  const update = useMutation({ mutationFn: () => api.updateTeamMember(member, role, true), onSuccess: async () => { onClose(); await queryClient.invalidateQueries({ queryKey: ["team"] }) } })
  const remove = useMutation({ mutationFn: () => api.updateTeamMember(member, role, false), onSuccess: async () => { onClose(); await queryClient.invalidateQueries({ queryKey: ["team"] }) } })
  return <Modal isOpen onClose={onClose} title="Edit member" footerStart={<Button variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate()}>Remove</Button>} actions={<Button disabled={update.isPending || role === member.role} onClick={() => update.mutate()}>{update.isPending ? "Saving…" : "Save changes"}</Button>}>
    <div className="space-y-4">
      <Field label="Email"><input className={formControl} value={member.email} readOnly /></Field>
      <Field label="Role"><SelectControl value={role} onChange={(event) => { setRole(event.target.value as MemberRole); update.reset() }}>{roles.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</SelectControl></Field>
      {(update.error ?? remove.error) && <div role="alert" className="text-[10px] text-[var(--red)]">{(update.error ?? remove.error)?.message}</div>}
    </div>
  </Modal>
}

function EmailSettings({ endpoints, topics }: { endpoints: EmailNotificationEndpoint[]; topics: NotificationTopic[] }) {
  const queryClient = useQueryClient()
  const setEnabled = useMutation({ mutationFn: (endpoint: EmailNotificationEndpoint) => api.setNotificationEndpointEnabled(endpoint.id, endpoint.revision, !endpoint.enabled), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["notification-endpoints"] }) })
  return <>
    {setEnabled.error && <div role="alert" className="mb-4 text-[10px] text-[var(--red)]">{setEnabled.error.message}</div>}
    <Table responsive="compact" className="min-w-[520px]">
      <TableHeader><TableRow><TableHead>Email</TableHead><TableHead>Notifies</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody columns={4} emptyMessage="No email destinations.">{endpoints.map((endpoint) => <TableRow key={endpoint.id}>
        <TableCell primary><div className="font-medium">{endpoint.email_address}</div></TableCell>
        <TableCell label="Notifies" wide>{topicLabels(endpoint, topics)}</TableCell>
        <TableCell label="Status"><Badge variant={endpoint.enabled ? "healthy" : "neutral"}>{endpoint.enabled ? "On" : "Paused"}</Badge></TableCell>
        <TableCell action className="pr-0"><Button variant="ghost" size="sm" disabled={setEnabled.isPending} onClick={() => setEnabled.mutate(endpoint)}>{endpoint.enabled ? "Pause" : "Enable"}</Button></TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </>
}

function DeliveryProblems({ problems, endpoints }: { problems: NotificationDelivery[]; endpoints: EmailNotificationEndpoint[] }) {
  return <div className="mt-8">
    <p className="mb-3 text-[11px] font-semibold text-[var(--ink)]">Delivery problems</p>
    <Table responsive="compact" className="min-w-[520px]">
      <TableHeader><TableRow><TableHead>Email</TableHead><TableHead>Problem</TableHead><TableHead className="pr-0 text-right">When</TableHead></TableRow></TableHeader>
      <TableBody columns={3}>{problems.map((problem) => <TableRow key={problem.id}>
        <TableCell primary><div className="font-medium">{endpoints.find((endpoint) => endpoint.id === problem.endpoint_id)?.email_address ?? "Removed endpoint"}</div></TableCell>
        <TableCell label="Problem" wide className="text-[var(--ink-soft)]">{problem.status_error ?? problem.last_error ?? titleCase(problem.state)}</TableCell>
        <TableCell label="When" action className="pr-0 text-right text-[10px] text-[var(--ink-soft)]">{formatDate(problem.sent_at ?? problem.available_at, true)}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </div>
}

function EmailModal({ onClose, defaultEmail, topics }: { onClose: () => void; defaultEmail: string; topics: NotificationTopic[] }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState(defaultEmail)
  const [selected, setSelected] = useState<string[]>([])
  const create = useMutation({ mutationFn: () => api.createNotificationEndpoint({ id: identifier(), email_address: email.trim(), topics: selected }), onSuccess: async () => { onClose(); await queryClient.invalidateQueries({ queryKey: ["notification-endpoints"] }) } })
  const toggle = (topic: string, checked: boolean) => {
    setSelected((current) => checked ? [...current, topic] : current.filter((item) => item !== topic))
    create.reset()
  }
  const ready = Boolean(email.trim() && selected.length)
  return <Modal isOpen onClose={onClose} title="Add email" actions={<Button disabled={!ready || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Adding…" : "Add email"}</Button>}>
    <div className="space-y-5">
      <Field label="Email address"><input className={formControl} type="email" value={email} onChange={(event) => { setEmail(event.target.value); create.reset() }} /></Field>
      <Fieldset label="Notify for">
        <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
          {topics.map((topic) => <label key={topic.id} className="flex items-center gap-2.5 text-[11px] text-[var(--ink)]">
            <input className="size-4 accent-[var(--ink)]" type="checkbox" checked={selected.includes(topic.id)} onChange={(event) => toggle(topic.id, event.target.checked)} />
            <span>{topic.label}</span>
          </label>)}
        </div>
      </Fieldset>
      {create.error && <div role="alert" className="text-[10px] text-[var(--red)]">{create.error.message}</div>}
    </div>
  </Modal>
}
