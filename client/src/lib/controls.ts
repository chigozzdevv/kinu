import type { ControlDefinition, ControlPreferences, ExposureSource } from "./api"

export interface ControlValues {
  automaticTriggers: string[]
  expiryDays: number
  observationMinutes: number
  requireRevokeApproval: boolean
  exposureSources: ExposureSource[]
}

export const defaultControls: ControlValues = {
  automaticTriggers: ["expiry", "drift"],
  expiryDays: 7,
  observationMinutes: 30,
  requireRevokeApproval: false,
  exposureSources: [],
}

export const controlTriggers = [
  ["expiry", "Expiry"],
  ["drift", "Configuration drift"],
  ["verified-exposure", "Verified exposure"],
] as const

const triggerEvents = {
  expiry: ["credential-expiring", "credential-rotation-due"],
  drift: ["credential-inventory-drift", "credential-provider-drift", "credential-runtime-drift"],
  "verified-exposure": ["credential-exposure-detected"],
} as const

export function controlsFromDefinition(definition: ControlDefinition | undefined): ControlValues {
  if (!definition) return defaultControls
  return {
    automaticTriggers: controlTriggers
      .filter(([trigger]) => triggerEvents[trigger].some((event) => definition.automatic_triggers.includes(event)))
      .map(([trigger]) => trigger),
    expiryDays: definition.rotate_before_expiry_seconds / 86400,
    observationMinutes: definition.maximum_observation_seconds / 60,
    requireRevokeApproval: definition.require_revoke_approval,
    exposureSources: definition.exposure_sources ?? [],
  }
}

export function controlsAreValid(values: ControlValues) {
  return values.automaticTriggers.length > 0
    && values.expiryDays >= 1
    && values.observationMinutes >= 1
    && (values.automaticTriggers.includes("verified-exposure") === (values.exposureSources.length > 0))
}

export function buildControlPreferences(values: ControlValues): ControlPreferences {
  return {
    automatic_triggers: values.automaticTriggers,
    rotate_before_expiry_seconds: values.expiryDays * 86400,
    maximum_observation_seconds: values.observationMinutes * 60,
    require_revoke_approval: values.requireRevokeApproval,
    exposure_sources: values.exposureSources,
  }
}
