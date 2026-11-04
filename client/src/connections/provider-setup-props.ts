import type { Connection } from "../types"

export type ProviderSetupProps = {
  onBack: () => void
  onClose: () => void
  onChanged: () => Promise<void>
  onCreated?: (connection: Connection) => Promise<void>
}
