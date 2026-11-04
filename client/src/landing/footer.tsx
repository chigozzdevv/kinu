import kinuLogo from "../assets/kinu-logo.svg"

export default function LandingFooter({ authenticated }: { authenticated: boolean }) {
  return <footer className="new-footer">
    <div className="new-footer-brand">
      <a href="#hero" aria-label="Kinu, back to top"><img src={kinuLogo} width="62" alt="Kinu" /></a>
      <small>© {new Date().getFullYear()} Kinu</small>
    </div>
    <nav aria-label="Footer navigation">
      <a href="#how-it-works">How it works</a>
      <a href="#rotation-example">Demo</a>
      <a href="#integrations">Integrations</a>
      <a href="#faq">FAQ</a>
      <a href={authenticated ? "/dashboard" : "/auth"}>{authenticated ? "Open dashboard" : "Sign in"}</a>
    </nav>
  </footer>
}
