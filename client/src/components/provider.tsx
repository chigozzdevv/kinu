import { providerName } from "../lib/format"
import { PlatformIcon } from "./platform-icon"

export function Provider({ value, label = true }: { value: string; label?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <span className="grid size-7 shrink-0 place-items-center">
        <PlatformIcon platform={value} />
      </span>
      <span className={label ? undefined : "sr-only"}>{providerName(value)}</span>
    </span>
  )
}
