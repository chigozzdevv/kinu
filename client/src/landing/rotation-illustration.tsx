import { useId, useRef } from "react"
import { Icon } from "@iconify/react"
import builderIcon from "@iconify-icons/logos/builder-io-icon.js"
import githubIcon from "@iconify-icons/logos/github-icon.js"
import { Activity, Check, EyeOff, Mail, Settings } from "lucide-react"
import { useRotationMotion } from "./rotation-motion"
import "./rotation-illustration.css"

const steps = ["Create", "Deploy", "Verify", "Revoke", "Done"]

export default function RotationIllustration() {
  const arrow = useId()
  const stage = useRef<HTMLDivElement>(null)
  const { layout, currentStep, hasReplacement, revoked, policyReady } = useRotationMotion(stage)
  return (
    <div className="rotation-illustration">
      <div ref={stage} className="ki-stage" role="img" aria-label="Drift monitoring, email, and GitHub feed Builder AI. It identifies the affected key and procedure. The isolated browser or API executor sends safe context to Builder AI and receives action proposals. Key values stay outside AI. Kinu creates, deploys, and verifies a replacement before revoking the old key.">
        <svg className="ki-wires" aria-hidden="true">
          <defs>
            <marker id={arrow} viewBox="0 0 6 6" refX="5" refY="3" markerWidth="5" markerHeight="5" orient="auto">
              <path d="M0 0 5 3 0 6" fill="none" strokeWidth="1" />
            </marker>
          </defs>
          {layout?.wires.map(({ name, d, arrow: directed }) => (
            <path key={name} data-wire={name} className="ki-wire" d={d} markerEnd={directed ? `url(#${arrow})` : undefined} />
          ))}
          {layout?.wires.map(({ name, d }) => (
            <path key={name} data-wire={name} className="ki-packet" d={d} opacity="0" />
          ))}
        </svg>
        <div className="ki-top">
          <div className="ki-sources">
            <p className="ki-eyebrow">Incident sources</p>
            <div className="ki-source"><Activity aria-hidden="true" /><span>Drift</span></div>
            <div className="ki-source"><Mail aria-hidden="true" /><span>Email</span></div>
            <div className="ki-source"><Icon icon={githubIcon} aria-hidden="true" /><span>GitHub</span></div>
          </div>
          <div className="ki-ai">
            <p className="ki-eyebrow">Understand &amp; plan</p>
            <div className="ki-builder"><Icon icon={builderIcon} aria-hidden="true" /><strong>Builder AI</strong></div>
            <div className={`ki-policy${policyReady ? " is-ready" : ""}`}>Allowed actions</div>
          </div>
          <div className="ki-context">
            <p className="ki-eyebrow">Affected resource</p>
            <div className="ki-incident">
              <dl className="ki-details">
                <div><dt>Key name</dt><dd><code>RESEND_API_KEY</code></dd></div>
                <div><dt>Procedure</dt><dd><code>playbook_a31f08</code></dd></div>
              </dl>
            </div>
          </div>
        </div>
        <div className="ki-execution-row">
          <div className="ki-execution">
            <p className="ki-eyebrow">Isolated execution</p>
            <div className="ki-browser">
              <div className="ki-browser-head"><Settings aria-hidden="true" /><strong>Browser / API</strong></div>
              <div className="ki-command ki-key-diff">
                <div className={`ki-key-line ki-key-old${revoked ? " is-retired" : ""}`}>
                  <span className="ki-key-sign">{revoked ? "−" : " "}</span><code>re_7f2a9c</code>
                </div>
                <div className="ki-key-line ki-key-new" hidden={!hasReplacement}>
                  <span className="ki-key-sign">+</span><code>re_9b4d1e</code>
                </div>
              </div>
            </div>
          </div>
          <div className="ki-privacy"><EyeOff aria-hidden="true" />Keys stay outside AI</div>
        </div>
        {layout && <>
          <span className="ki-wire-label" style={{ left: layout.proposal.x, top: layout.proposal.y }}>Action <br />proposal</span>
          <span className="ki-wire-label" style={{ left: layout.context.x, top: layout.context.y }}>Safe <br />context</span>
        </>}
        <div className="ki-lifecycle">
          {steps.map((label, i) => {
            const done = currentStep > i || currentStep === steps.length - 1
            const state = done ? "is-done" : currentStep === i ? "is-current" : ""
            return (
              <div className={`ki-step ${state}`} key={label}>
                <span className="ki-step-symbol">
                  <span className="ki-step-number">{i + 1}</span>
                  <span className="ki-step-check"><Check aria-hidden="true" /></span>
                </span>
                <span className="ki-step-label">{label}</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
