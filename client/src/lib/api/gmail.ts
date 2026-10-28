import { request, workspacePath } from "./http"

export interface GmailSender {
  sender: string
  provider_connection_id: string
}

export interface GmailMailbox {
  id: string
  organisation_id: string
  email: string
  status: "setup-required" | "ready" | "reauthentication-required" | "disabled"
  senders: GmailSender[]
  connected_at: string
  last_checked_at: string | null
  revision: number
}

export const gmail = {
  async listGmail(): Promise<GmailMailbox[]> {
    return request(`${workspacePath()}/connections/gmail`)
  },
  async beginGmail(mailboxId?: string): Promise<{ authorization_url: string }> {
    return request(`${workspacePath()}/connections/gmail`, { method: "POST", body: JSON.stringify({ mailbox_id: mailboxId ?? null }) })
  },
  async getGmail(mailboxId: string): Promise<GmailMailbox> {
    return request(`${workspacePath()}/connections/gmail/${mailboxId}`)
  },
  async saveGmail(mailbox: GmailMailbox, senders: GmailSender[]): Promise<GmailMailbox> {
    return request(`${workspacePath()}/connections/gmail/${mailbox.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: mailbox.revision, senders }) })
  },
  async disconnectGmail(mailbox: GmailMailbox): Promise<GmailMailbox> {
    return request(`${workspacePath()}/connections/gmail/${mailbox.id}/disconnect`, { method: "POST", body: JSON.stringify({ expected_revision: mailbox.revision }) })
  },
}
