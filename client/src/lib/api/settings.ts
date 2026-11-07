import type { Features } from "./types"
import type { AccountProfile, AccountSession, Identifier, EmailNotificationEndpoint, NotificationDelivery, NotificationTopic, OrganisationMembership, TeamMember, MemberRole } from "../../types"
import type { CreateNotificationEndpointInput } from "./types"
import { request, workspacePath } from "./http"

export const settings = {
  async getFeatures(): Promise<Features> {
    return request(`${workspacePath()}/features`)
  },

  async getSession(): Promise<AccountSession> {
    return request("/v1/session")
  },

  async createOrganisation(name: string): Promise<OrganisationMembership> {
    return request("/v1/organisations", {
      method: "POST",
      body: JSON.stringify({ name }),
    })
  },

  async getProfile(): Promise<AccountProfile> {
    return request(`${workspacePath()}/settings/profile`)
  },

  async updateProfile(expectedRevision: number, displayName: string): Promise<AccountProfile> {
    return request(`${workspacePath()}/settings/profile`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, display_name: displayName }),
    })
  },

  async getTeam(): Promise<TeamMember[]> {
    return request(`${workspacePath()}/settings/team`)
  },

  async inviteTeamMember(email: string, role: MemberRole): Promise<TeamMember> {
    return request(`${workspacePath()}/settings/team/invitations`, {
      method: "POST",
      body: JSON.stringify({ email, role }),
    })
  },

  async updateTeamMember(member: TeamMember, role: MemberRole, enabled: boolean): Promise<TeamMember> {
    return request(`${workspacePath()}/settings/team/members/${member.id}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: member.revision, role, enabled }),
    })
  },

  async cancelTeamInvitation(member: TeamMember): Promise<TeamMember> {
    return request(`${workspacePath()}/settings/team/invitations/${member.id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: member.revision }),
    })
  },

  async getNotificationSettings(): Promise<{ email_enabled: boolean }> {
    return request(`${workspacePath()}/notifications/settings`)
  },

  async getNotificationTopics(): Promise<NotificationTopic[]> {
    return request(`${workspacePath()}/notifications/topics`)
  },

  async getNotificationEndpoints(): Promise<EmailNotificationEndpoint[]> {
    return request(`${workspacePath()}/notifications/endpoints`)
  },

  async getNotificationDeliveries(): Promise<NotificationDelivery[]> {
    return request(`${workspacePath()}/notifications/deliveries`)
  },

  async createNotificationEndpoint(input: CreateNotificationEndpointInput): Promise<EmailNotificationEndpoint> {
    return request(`${workspacePath()}/notifications/endpoints`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

  async setNotificationEndpointEnabled(endpointId: Identifier, expectedRevision: number, enabled: boolean): Promise<EmailNotificationEndpoint> {
    return request(`${workspacePath()}/notifications/endpoints/${endpointId}/state`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, enabled }),
    })
  }
}

