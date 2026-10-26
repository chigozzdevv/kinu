import { ArrowRight } from "lucide-react"
import { useEffect, useState } from "react"
import kinuLogo from "../../assets/kinu-logo.svg"
import { Boundary } from "../components/boundary"
import { Coverage } from "../components/coverage"
import { Governance } from "../components/governance"
import { Integrations } from "../components/integrations"
import "../landing.css"

export function HomePage({ authenticated }: { authenticated: boolean }) {
  const destination = authenticated ? "/dashboard" : "/auth"
  const action = authenticated ? "Open dashboard" : "Sign in"
  const heroAction = authenticated ? "Open dashboard" : "Get started"
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const updateHeader = () => setScrolled(window.scrollY > 16)

    updateHeader()
    window.addEventListener("scroll", updateHeader, { passive: true })
    return () => window.removeEventListener("scroll", updateHeader)
  }, [])

  return (
    <div className="landing">
      <a className="landing__skip" href="#main-content">Skip to content</a>

      <header className={`landing-header${scrolled ? " landing-header--scrolled" : ""}`}>
        <a href="/" aria-label="Kinu home"><img src={kinuLogo} alt="Kinu" /></a>
        <nav aria-label="Landing navigation">
          <a href="#product">Product</a>
          <a href="#coverage">How it works</a>
        </nav>
        <a className="landing-header__action" href={destination}>{action}</a>
      </header>

      <main id="main-content">
        <section id="product" className="landing-hero">
          <div className="landing-hero__copy">
            <h1>
              <span>When a key expires or <span className="landing-hero__keep">is exposed, <strong>Kinu’s agents</strong></span></span>
              <span>plan and rotate it everywhere it’s used without ever seeing the secret.</span>
            </h1>
            <a className="landing-button" href={destination}>{heroAction}<ArrowRight /></a>
          </div>
          <div className="landing-hero__visual"><Boundary /></div>
        </section>

        <Coverage />

        <Governance />

        <Integrations />

        <section className="landing-cta">
          <div className="landing-cta__panel">
            <div className="landing-cta__copy">
              <h2>Rotate the next key with Kinu.</h2>
            </div>
            <a className="landing-cta__action" href={destination}>{heroAction}<ArrowRight /></a>
          </div>
        </section>
      </main>
    </div>
  )
}
