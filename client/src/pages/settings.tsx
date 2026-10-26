import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { Detail, DetailList, DetailTabs } from "../components/detail"
import { PageHeader } from "../components/header"
import { IdentityProvider } from "../components/identity"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, Fieldset, SelectControl, formControl } from "../components/workspace"
import { api } from "../lib/api"
import { titleCase } from "../lib/format"
import type { EmailNotificationEndpoint, MemberRole, NotificationTopic, TeamMember } from "../types"

type Tab = "profile" | "team" | "notifications"

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
  const [tab, setTab] = useState<Tab>("profile")
  const [inviting, setInviting] = useState(false)
  const [editingMember, setEditingMember] = useState<TeamMember | null>(null)
  const [addingEmail, setAddingEmail] = useState(false)
  const profile = useQuery({ queryKey: ["profile"], queryFn: () => api.getProfile() })
  const administrator = profile.data?.role === "administrator"
  const team = useQuery({ queryKey: ["team"], queryFn: () => api.getTeam(), enabled: Boolean(administrator && tab === "team") })
  const endpoints = useQuery({ queryKey: ["notification-endpoints"], queryFn: () => api.getNotificationEndpoints(), enabled: tab === "notifications" })
  const topics = useQuery({ queryKey: ["notification-topics"], queryFn: () => api.getNotificationTopics(), enabled: tab === "notifications" })
  useEffect(() => {
    if (profile.data && !administrator && tab === "team") setTab("profile")
  }, [administrator, profile.data, tab])
  const tabs = administrator
    ? [{ id: "profile" as const, label: "Profile" }, { id: "team" as const, label: "Team" }, { id: "notifications" as const, label: "Notifications" }]
    : [{ id: "profile" as const, label: "Profile" }, { id: "notifications" as const, label: "Notifications" }]
  const actions = tab === "team"
    ? <Button onClick={() => setInviting(true)}><Plus className="size-3.5" /> Invite member</Button>
    : tab === "notifications"
      ? <Button onClick={() => setAddingEmail(true)}><Plus className="size-3.5" /> Add email</Button>
      : undefined

  const activeLoading = profile.isLoading || (tab === "team" && team.isLoading) || (tab === "notifications" && (endpoints.isLoading || topics.isLoading))
  if (activeLoading) return <div className="page"><Loading /></div>
  const error = profile.error ?? (tab === "team" ? team.error : null) ?? (tab === "notifications" ? endpoints.error ?? topics.error : null)
  if (error) return <div className="page"><Failure error={error} /></div>

  return <div className="page">
    <PageHeader title="Settings" actions={actions} />
    <DetailTabs items={tabs} value={tab} onChange={setTab} />
    {tab === "profile" && <ProfileSettings profile={profile.data!} />}
    {tab === "team" && <TeamSettings members={team.data!} currentId={profile.data!.id} onEdit={setEditingMember} />}
    {tab === "notifications" && <EmailSettings endpoints={endpoints.data!} topics={topics.data!} />}
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
  return <div className="max-w-[872px] space-y-7">
    <div className="w-[calc(50%+84px)] max-w-full"><Field label="Name"><input className={formControl} value={name} onChange={(event) => { setName(event.target.value); save.reset() }} /></Field></div>
    <DetailList>
      <Detail label="Email">{profile.email}</Detail>
      <Detail label="Connected via"><IdentityProvider value={profile.connected_via} /></Detail>
    </DetailList>
    {save.error && <div role="alert" className="text-[10px] text-[var(--red)]">{save.error.message}</div>}
    <Button disabled={!name.trim() || name.trim() === profile.display_name || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save changes"}</Button>
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
    <Table>
      <TableHeader><TableRow><TableHead>Member</TableHead><TableHead>Role</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody>{members.map((member) => <TableRow key={member.id}>
        <TableCell><div className="font-medium">{member.display_name ?? member.email}</div>{member.display_name && <div className="mt-1 text-[10px] text-[var(--ink-muted)]">{member.email}</div>}</TableCell>
        <TableCell>{titleCase(member.role)}</TableCell>
        <TableCell><Badge variant={member.status === "active" ? "healthy" : "neutral"}>{titleCase(member.status)}</Badge></TableCell>
        <TableCell className="pr-0"><div className="flex justify-end">{member.status === "pending" ? <Button variant="ghost" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate(member)}>Cancel</Button> : member.id !== currentId ? <Button variant="ghost" size="sm" onClick={() => onEdit(member)}>Edit</Button> : null}</div></TableCell>
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
  if (!endpoints.length) return <div className="py-12 text-center text-[11px] text-[var(--ink-muted)]">No email destinations.</div>
  return <>
    {setEnabled.error && <div role="alert" className="mb-4 text-[10px] text-[var(--red)]">{setEnabled.error.message}</div>}
    <Table>
      <TableHeader><TableRow><TableHead>Email</TableHead><TableHead>Notifies</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody>{endpoints.map((endpoint) => <TableRow key={endpoint.id}>
        <TableCell><div className="font-medium">{endpoint.email_address}</div></TableCell>
        <TableCell>{topicLabels(endpoint, topics)}</TableCell>
        <TableCell><Badge variant={endpoint.enabled ? "healthy" : "neutral"}>{endpoint.enabled ? "On" : "Paused"}</Badge></TableCell>
        <TableCell className="pr-0"><div className="flex justify-end"><Button variant="ghost" size="sm" disabled={setEnabled.isPending} onClick={() => setEnabled.mutate(endpoint)}>{endpoint.enabled ? "Pause" : "Enable"}</Button></div></TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </>
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
