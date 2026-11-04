import { useEffect, useRef } from "react"
import LandingActions from "../landing/landing-actions"
import LandingHeader from "../landing/landing-header"
import RotationIllustration from "../landing/rotation-illustration"
import Demo from "../landing/demo"
import Integrations from "../landing/integrations"
import Faq from "../landing/faq"
import LandingFooter from "../landing/footer"
import SelfHost from "../landing/self-host"
import "../landing/fonts/fonts.css"
import "../landing/hero.css"
import "../landing/closing.css"

export default function HomePage({ authenticated }: { authenticated: boolean }) {
  const subtitle = useRef<HTMLParagraphElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const openSelfHost = () => dialog.current?.showModal()

  useEffect(() => {
    document.title = "Kinu · Agentic key rotation"
    if (subtitle.current) {
      document.querySelector('meta[name="description"]')?.setAttribute("content", subtitle.current.textContent)
    }
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#ffffff")
    document.getElementById(window.location.hash.slice(1))?.scrollIntoView()
  }, [])

  return (
    <div className="new-landing">
      <LandingHeader authenticated={authenticated} />

      <main id="hero" tabIndex={-1}>
        <div className="new-hero-layout">
          <section className="new-hero" aria-labelledby="hero-title">
            <h1 id="hero-title">Agents that rotate your keys.</h1>
            <p ref={subtitle} className="new-subtitle">
              When a key leaks, nears expiry, or drifts, Kinu agents rotate it through an API or computer use in an isolated browser <strong>without ever seeing</strong> the key.
            </p>
            <LandingActions authenticated={authenticated} onSelfHost={openSelfHost} />
          </section>
          <section id="how-it-works" className="new-illustration" aria-label="How Kinu rotates a key">
            <RotationIllustration />
          </section>
        </div>
        <Demo />
        <Integrations />
        <Faq />
        <section className="new-closing-cta" aria-labelledby="closing-cta-title">
          <h2 id="closing-cta-title">Start with one key.</h2>
          <p>Connect a provider and choose when Kinu rotates your key.</p>
          <LandingActions authenticated={authenticated} onSelfHost={openSelfHost} />
        </section>
      </main>
      <LandingFooter authenticated={authenticated} />
      <SelfHost dialog={dialog} />
    </div>
  )
}
