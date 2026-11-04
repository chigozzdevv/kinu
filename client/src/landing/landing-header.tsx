import { ArrowUpRight } from "lucide-react"
import { useEffect, useState } from "react"
import kinuLogo from "../assets/kinu-logo.svg"

export default function LandingHeader({ authenticated }: { authenticated: boolean }) {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 0)
    update()
    window.addEventListener("scroll", update, { passive: true })
    return () => window.removeEventListener("scroll", update)
  }, [])

  return <>
    <a href="#hero" className="new-skip">Skip to content</a>
    <header className={`new-header${scrolled ? " new-header--scrolled" : ""}`}>
      <div className="new-header-inner">
        <a className="new-brand" href="/" aria-label="Kinu home">
          <img src={kinuLogo} width="78" height="32" alt="Kinu" />
        </a>
        <nav aria-label="Main navigation">
          <a href="#how-it-works">How it works</a>
          <a href={authenticated ? "/dashboard" : "/auth"}>{authenticated ? "Open dashboard" : "Sign in"} <ArrowUpRight aria-hidden="true" size={15} /></a>
        </nav>
      </div>
    </header>
  </>
}
