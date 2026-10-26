import {
  Braces,
  KeyRound,
  MonitorCog,
  MousePointer2,
} from "lucide-react"
import kinuLogo from "../../assets/kinu-logo.svg"

function KinuExecutor() {
  return (
    <div className="landing-methods__kinu" aria-label="Kinu">
      <img src={kinuLogo} alt="" />
    </div>
  )
}

function ConnectorLine() {
  return <span className="landing-methods__connector" aria-hidden="true" />
}

function ApiIllustration() {
  return (
    <div
      className="landing-methods__visual landing-methods__visual--api"
      aria-label="Kinu sends create and retire operations directly to the provider API"
    >
      <KinuExecutor />
      <ConnectorLine />
      <div className="landing-methods__api-routes">
        <svg viewBox="0 0 318 176" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 88 C34 88 28 44 68 44 H296" />
          <path d="M0 88 C34 88 28 132 68 132 H296" />
        </svg>
        <div className="landing-methods__api-route">
          <strong>Create replacement</strong>
          <span className="landing-methods__key-action landing-methods__key-action--create">
            <KeyRound aria-hidden="true" />
            <i aria-hidden="true">+</i>
          </span>
        </div>
        <div className="landing-methods__api-route">
          <strong>Retire old key</strong>
          <span className="landing-methods__key-action landing-methods__key-action--retire">
            <KeyRound aria-hidden="true" />
            <i aria-hidden="true">×</i>
          </span>
        </div>
      </div>
    </div>
  )
}

function ComputerUseIllustration() {
  return (
    <div
      className="landing-methods__visual landing-methods__visual--browser"
      aria-label="Kinu operates the provider console inside an isolated browser VM"
    >
      <KinuExecutor />
      <ConnectorLine />
      <div className="landing-methods__browser">
        <div className="landing-methods__browser-bar">
          <span aria-hidden="true"><i /><i /><i /></span>
        </div>
        <div className="landing-methods__console">
          <div className="landing-methods__console-row">
            <span><KeyRound aria-hidden="true" />API keys</span>
            <b>Create replacement</b>
            <MousePointer2 aria-hidden="true" />
          </div>
        </div>
      </div>
    </div>
  )
}

export function Coverage() {
  return (
    <section id="coverage" className="landing-methods">
      <div className="landing-methods__copy">
        <h2>With an API or without one, Kinu can still rotate your keys</h2>
      </div>

      <div className="landing-methods__grid">
        <article className="landing-methods__method">
          <header>
            <Braces aria-hidden="true" />
            <div>
              <h3>Provider API</h3>
              <p>
                Kinu connects directly to the provider through your secret
                manager auth reference to create the replacement and retire
                the old key.
              </p>
            </div>
          </header>

          <ApiIllustration />

        </article>

        <article className="landing-methods__method">
          <header>
            <MonitorCog aria-hidden="true" />
            <div>
              <h3>Computer Use</h3>
              <p>
                When there’s no API, Kinu completes the same rotation through
                the provider’s console in an isolated browser using your
                approved playbook.
              </p>
            </div>
          </header>

          <ComputerUseIllustration />

        </article>
      </div>
    </section>
  )
}
