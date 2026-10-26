import type { ElementType } from "react"
import { Icon } from "@iconify/react"
import githubIcon from "@iconify-icons/logos/github-icon.js"
import oracle from "@iconify-icons/logos/oracle.js"
import segmentIcon from "@iconify-icons/logos/segment-icon.js"
import sendgridIcon from "@iconify-icons/logos/sendgrid-icon.js"
import snowflakeIcon from "@iconify-icons/logos/snowflake-icon.js"
import stripe from "@iconify-icons/logos/stripe.js"
import { Globe2, KeyRound } from "lucide-react"
import { providerName } from "../lib/format"

type ProviderVisual = { logo?: typeof sendgridIcon; icon?: ElementType; wide?: boolean }

const visuals: Record<string, ProviderVisual> = {
  sendgrid: { logo: sendgridIcon },
  stripe: { logo: stripe, wide: true },
  github: { logo: githubIcon },
  "internal-vendor": { icon: Globe2 },
  netsuite: { logo: oracle, wide: true },
  segment: { logo: segmentIcon },
  snowflake: { logo: snowflakeIcon },
  kinu: { icon: KeyRound },
}

export function Provider({ value, label = true }: { value: string; label?: boolean }) {
  const visual = visuals[value] ?? { icon: KeyRound }
  const Fallback = visual.icon

  return (
    <span className="inline-flex items-center gap-2.5">
      <span className="grid size-7 shrink-0 place-items-center" aria-hidden="true">
        {visual.logo ? <Icon icon={visual.logo} className={visual.wide ? "w-6" : "size-5"} /> : Fallback ? <Fallback className="size-4 text-[var(--ink-soft)]" /> : null}
      </span>
      {label && <span>{providerName(value)}</span>}
    </span>
  )
}
