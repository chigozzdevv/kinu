import { useFeatures } from "../lib/features"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "../components/header"
import { IntegrationGrid, type IntegrationKind } from "../components/integration"
import { Failure, Loading, Notice } from "../components/state"
import type { Connection, ConnectionRole, Playbook } from "../types"
import { api } from "../lib/api"
import { connectionCallbackIntegration, type BeforeAuthorization } from "../lib/callback"
import { ConnectionTypes } from "../components/connection-types"
import { AwsSetup } from "./aws"
import { HostingSetup } from "./hosting"
import { GmailSetup } from "./gmail"
import { ResendSetup } from "./resend"
import { GitHubSetup } from "./github"
import { ComputerUseSetup } from "./computer-use"
import { CustomApiSetup } from "./custom-api"

type ConnectionSetupProps = {
  onClose: () => void
  playbooks: Playbook[]
  connections: Connection[]
  onChanged: () => Promise<void>
  onCreated?: (connection: Connection) => Promise<void>
  beforeAuthorization?: BeforeAuthorization
  integrations?: IntegrationKind[]
  initialRoles?: ConnectionRole[]
  initialIntegration?: IntegrationKind | null
}

export function ConnectionSetup({ onClose, playbooks, connections, onChanged, onCreated, initialRoles = [], initialIntegration = null, integrations, beforeAuthorization }: ConnectionSetupProps) {
  const [integration, setIntegration] = useState<IntegrationKind | null>(() => connectionCallbackIntegration() ?? initialIntegration)
  const [category, setCategory] = useState<ConnectionRole | null>(initialRoles[0] ?? null)
  const features = useFeatures()
  const integrationSettings = useQuery({ queryKey: ["integrations"], queryFn: () => api.getIntegrations(), enabled: features.data?.hosting === true })
  const configured = (id: string) => integrationSettings.data?.integrations.some((item) => item.id === id && item.configured) === true
  const available = {
    vercel: features.data?.hosting === true && configured("vercel"),
    railway: features.data?.hosting === true && configured("railway"),
    render: features.data?.hosting === true,
    resend: features.data?.provider_api === true,
    github: features.data?.github === true,
    gmail: features.data?.gmail === true,
    aws: features.data?.aws_connections === true,
    "custom-api": features.data?.provider_api === true,
    "computer-use": features.data?.browser_setup === true,
  }
  const requestedProvider = category === "provider"
  const requestedCloud = category === "runtime" || category === "secret-store"
  const requestedIncident = category === "incident"
  const visible: IntegrationKind[] = integrations ?? (requestedIncident
    ? ["gmail", "github"]
    : requestedProvider
    ? ["resend", "custom-api", "computer-use"]
    : requestedCloud
      ? category === "runtime" ? ["vercel", "railway", "render", "aws"] : ["aws"]
      : [])
  const unconfigured = { vercel: features.data?.hosting === true && !configured("vercel"), railway: features.data?.hosting === true && !configured("railway") }
  const connected = {
    resend: connections.some((item) => item.platform === "resend" && item.status === "ready"),
    gmail: connections.some((item) => item.platform === "gmail" && item.status === "ready"),
    github: connections.some((item) => item.platform === "github" && item.roles.includes("incident")),
    aws: connections.some((item) => item.platform === "aws" && item.status === "ready"),
    "custom-api": connections.some((item) => item.interface === "api" && item.roles.includes("provider") && item.http !== null),
    "computer-use": connections.some((item) => item.interface === "browser"),
  }

  if (features.isLoading || integrationSettings.isLoading) return <div className="page"><Loading /></div>
  if (features.error) return <div className="page"><Failure error={features.error} /></div>
  if (!integration && !category) return <div className="page max-w-[960px]">
    <PageHeader eyebrow="Inventory / Connections" title="Add connection" onBack={onClose} />
    <ConnectionTypes onSelect={setCategory} />
  </div>
  if (integration && (!available[integration] || (integrations && !integrations.includes(integration)))) return <div className="page"><PageHeader title="Add connection" onBack={onClose} /><Notice>This connection is not available yet.</Notice></div>

  if (integration === "vercel" || integration === "railway" || integration === "render") return <HostingSetup platform={integration} {...{ onClose, onChanged, onCreated }} beforeAuthorization={beforeAuthorization} onBack={() => connectionCallbackIntegration() && beforeAuthorization ? onClose() : setIntegration(null)} />
  if (integration === "custom-api") return <CustomApiSetup {...{ onClose, onChanged, onCreated }} onBack={() => setIntegration(null)} />
  if (integration === "resend") return <ResendSetup {...{ onClose, onChanged, onCreated }} onBack={() => setIntegration(null)} />
  if (integration === "computer-use") return <ComputerUseSetup {...{ onClose, onChanged, onCreated, connections, playbooks }} onBack={() => setIntegration(null)} />
  if (integration === "gmail") return <GmailSetup {...{ onClose, onChanged, onCreated, connections }} beforeAuthorization={beforeAuthorization} onBack={() => connectionCallbackIntegration() && beforeAuthorization ? onClose() : setIntegration(null)} />
  if (integration === "github") return <GitHubSetup {...{ onClose, onChanged, onCreated, connections }} beforeAuthorization={beforeAuthorization} onBack={() => connectionCallbackIntegration() && beforeAuthorization ? onClose() : setIntegration(null)} />
  if (integration === "aws") return <AwsSetup {...{ onClose, onChanged, onCreated }} onBack={() => setIntegration(null)}
    requiredRole={category === "runtime" || category === "secret-store" ? category : undefined} />

  return <div className="page max-w-[960px]">
    <PageHeader eyebrow="Inventory / Connections" title="Add connection" onBack={() => initialRoles.length ? onClose() : setCategory(null)} />
    {integrationSettings.error && <Failure error={integrationSettings.error} />}
    <IntegrationGrid visible={visible} connected={connected} available={available} unconfigured={integrationSettings.data ? unconfigured : undefined} onSelect={setIntegration} />
  </div>
}
