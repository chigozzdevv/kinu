import { request, workspacePath } from "./http"

export interface Integration {
  id: string
  name: string
  category: string
  authorization: string
  configured: boolean
  fields: string[]
  callback_url: string | null
}

export interface IntegrationSettings {
  editable: boolean
  integrations: Integration[]
}

export const integrations = {
  getIntegrations(): Promise<IntegrationSettings> {
    return request(`${workspacePath()}/integrations`)
  },
}
