import { Icon } from "@iconify/react"
import cloudflareIcon from "@iconify-icons/logos/cloudflare-icon.js"
import githubIcon from "@iconify-icons/logos/github-icon.js"
import resendIcon from "@iconify-icons/logos/resend-icon.js"
import stripeIcon from "@iconify-icons/logos/stripe.js"
import {
  CalendarClock,
  KeyRound,
  MousePointerClick,
  Server,
} from "lucide-react"
import type { ReactNode } from "react"
import kinuLogo from "../../assets/kinu-logo.svg"

type EntryProps = {
  icon: ReactNode
  label: string
  detail?: string
}

function Entry({ icon, label, detail }: EntryProps) {
  return (
    <span className="landing-orbit__entry">
      <span className="landing-orbit__entry-icon">{icon}</span>
      <small>{label}</small>
      {detail && <em>{detail}</em>}
    </span>
  )
}

export function Boundary() {
  return (
    <div
      id="boundary"
      className="landing-orbit"
      aria-label="Kinu coordinates rotation triggers, credential providers, secret managers, and runtimes"
    >
      <svg className="landing-orbit__lines" viewBox="0 0 680 400" preserveAspectRatio="none" aria-hidden="true">
        <path d="M260 70 H272 C284 70 290 80 290 94 V144 C290 158 298 168 306 174" />
        <path d="M420 70 H408 C396 70 390 80 390 94 V144 C390 158 382 168 374 174" />
        <path d="M260 330 H272 C284 330 290 320 290 306 V256 C290 242 298 232 306 226" />
        <path d="M420 330 H408 C396 330 390 320 390 306 V256 C390 242 382 232 374 226" />
      </svg>

      <section className="landing-orbit__card landing-orbit__card--triggers" aria-label="Triggers">
        <h2><CalendarClock />Rotation triggers</h2>
        <div className="landing-orbit__entries landing-orbit__entries--four">
          <Entry icon={<Icon icon={githubIcon} />} label="GitHub" />
          <Entry icon={<CalendarClock />} label="Exposure" />
          <Entry icon={<CalendarClock />} label="Expiry" />
          <Entry icon={<MousePointerClick />} label="Manual" />
        </div>
      </section>

      <section className="landing-orbit__card landing-orbit__card--provider" aria-label="Credential provider">
        <h2><KeyRound />Credential provider</h2>
        <div className="landing-orbit__entries landing-orbit__entries--four">
          <Entry icon={<Icon icon={resendIcon} />} label="Resend" />
          <Entry icon={<KeyRound />} label="API provider" />
          <Entry icon={<Icon icon={stripeIcon} />} label="Stripe" />
          <Entry icon={<Icon icon={cloudflareIcon} />} label="Cloudflare" />
        </div>
      </section>

      <section className="landing-orbit__card landing-orbit__card--secrets" aria-label="Secret manager">
        <h2><KeyRound />Secret manager</h2>
        <div className="landing-orbit__entries">
          <Entry icon={<KeyRound />} label="Secret store" />
        </div>
      </section>

      <section className="landing-orbit__card landing-orbit__card--runtime" aria-label="Runtime">
        <h2><Server />Runtime</h2>
        <div className="landing-orbit__entries">
          <Entry icon={<Server />} label="Workloads" />
        </div>
      </section>

      <div className="landing-orbit__hub" aria-label="Kinu control plane">
        <span><img src={kinuLogo} alt="Kinu" /></span>
      </div>
    </div>
  )
}
