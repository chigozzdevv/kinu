import {
  Ban,
  Database,
  KeyRound,
  LockKeyhole,
  RotateCcw,
  ServerCog,
  ShieldCheck,
} from "lucide-react"
import type { ReactNode } from "react"

type StageProps = {
  className: string
  guarded?: boolean
  icon: ReactNode
  label: string
}

function GovernanceStage({ className, guarded = false, icon, label }: StageProps) {
  return (
    <div
      className={`landing-governance__stage ${className}${guarded ? " landing-governance__stage--guarded" : ""}`}
    >
      <span>{icon}</span>
      <strong>{label}</strong>
    </div>
  )
}

export function Governance() {
  return (
    <section id="governance" className="landing-governance">
      <div className="landing-governance__inner">
        <div className="landing-governance__copy">
          <h2>
            <span>You decide what</span>
            <span>Kinu can change.</span>
          </h2>
          <p>
            Set what Kinu can do automatically, when it must ask for approval,
            and when it should roll back.
          </p>
        </div>

        <div
          className="landing-governance__map"
          aria-label="Kinu automatically creates, stores, deploys, verifies, and observes under policy. Successful runs pass through approval when required before revocation. Failed checks roll back."
        >
          <svg viewBox="0 0 720 330" preserveAspectRatio="none" aria-hidden="true">
            <path className="landing-governance__main-path" d="M58 140 H446" />
            <path
              className="landing-governance__approval-path"
              d="M446 140 C500 140 500 108 576 108 H684"
            />
            <path
              className="landing-governance__recovery-path"
              d="M446 140 C500 140 500 253 576 253"
            />
          </svg>

          <GovernanceStage
            className="landing-governance__stage--create"
            icon={<KeyRound aria-hidden="true" />}
            label="Create"
          />
          <GovernanceStage
            className="landing-governance__stage--store"
            icon={<Database aria-hidden="true" />}
            label="Store"
          />
          <GovernanceStage
            className="landing-governance__stage--deploy"
            icon={<ServerCog aria-hidden="true" />}
            label="Deploy"
          />
          <GovernanceStage
            className="landing-governance__stage--verify"
            icon={<ShieldCheck aria-hidden="true" />}
            label="Verify & observe"
          />
          <GovernanceStage
            className="landing-governance__stage--approval"
            guarded
            icon={<LockKeyhole aria-hidden="true" />}
            label="Approval if required"
          />
          <GovernanceStage
            className="landing-governance__stage--revoke"
            guarded
            icon={<Ban aria-hidden="true" />}
            label="Revoke old key"
          />
          <GovernanceStage
            className="landing-governance__stage--rollback"
            icon={<RotateCcw aria-hidden="true" />}
            label="Roll back"
          />
        </div>
      </div>
    </section>
  )
}
