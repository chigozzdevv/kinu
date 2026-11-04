import { ArrowRight } from "lucide-react"

export default function LandingActions({ authenticated, onSelfHost }: { authenticated: boolean; onSelfHost: () => void }) {
  return <div className="new-actions">
    <a className="new-primary-action" href={authenticated ? "/dashboard" : "/auth"}>
      {authenticated ? "Open dashboard" : "Get started"} <ArrowRight aria-hidden="true" size={16} />
    </a>
    <button type="button" className="new-secondary-action" aria-haspopup="dialog" onClick={onSelfHost}>Self-host</button>
  </div>
}
