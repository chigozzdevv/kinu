import { useEffect, type ReactNode } from "react"
import { ArrowUpRight } from "lucide-react"
import kinuLogo from "../assets/kinu-logo.svg"
import "../landing/fonts/fonts.css"
import "./public-info.css"

type Page = "vercel" | "terms" | "privacy"
type Article = { title: string; introduction: string; sections: { title: string; body: ReactNode }[] }
const support = <a href="mailto:support.kinu@muwa.io">support.kinu@muwa.io</a>
const links = [
  { page: "vercel", href: "/docs/vercel", label: "Vercel guide" },
  { page: "terms", href: "/terms", label: "Terms" },
  { page: "privacy", href: "/privacy", label: "Privacy" },
] as const

const articles: Record<Page, Article> = {
  vercel: {
    title: "Connect Vercel to Kinu.",
    introduction: "Connect your projects, choose the keys Kinu manages, and verify each replacement before the old key is revoked.",
    sections: [
      { title: "Before you connect", body: <p>You need a Kinu workspace and a Vercel account that can authorize access to the projects you want to manage. Connect a key provider in Kinu so it can create and revoke the affected keys.</p> },
      { title: "Connect your account", body: <ol>
        <li>In Kinu, open <strong>Connections → Add connection → Hosting → Vercel</strong>.</li>
        <li>Review the permissions in Vercel and choose the projects Kinu may access.</li>
        <li>Return to Kinu, open <strong>Credentials → Add credential</strong>, and select your Vercel project and production key variables.</li>
        <li>Choose each key’s provider, add the workload health URL, and review the rotation controls.</li>
      </ol> },
      { title: "Permissions", body: <>
        <p>Kinu requests Projects read access, Deployments read/write access, and Global Project Environment Variables read/write access. Vercel also provides read access to Kinu’s integration configuration.</p>
        <p>These permissions let Kinu discover projects, update existing production key variables, and deploy replacements. Choose project access during installation and select the credentials to manage inside Kinu.</p>
      </> },
      { title: "Verify the loaded key", body: <>
        <p>Your workload’s health endpoint must check its loaded key with the provider and return fresh proof. Echo the <code>X-Kinu-Probe</code> request header as <code>nonce</code>, and return the SHA-256 fingerprint of each managed variable:</p>
        <pre><code>{`{
  "ok": true,
  "nonce": "VALUE_OF_X_KINU_PROBE",
  "fingerprints": {
    "API_KEY": "SHA256_OF_LOADED_KEY"
  }
}`}</code></pre>
        <p>Disable caching and return fingerprints rather than raw keys. Kinu keeps the predecessor active until the replacement is deployed and verified. Required approvals can pause the rotation before revocation.</p>
      </> },
      { title: "Disconnect or get help", body: <p>Open the connection in Kinu, choose <strong>Edit → Disconnect</strong>, or uninstall Kinu through Vercel’s integration settings to revoke its provider access. Disconnecting does not undo previous deployments or restore revoked keys. For setup help, contact {support}.</p> },
    ],
  },
  terms: {
    title: "Terms of use.",
    introduction: "These terms cover the hosted Kinu service at kinu.web.app. Separately distributed Kinu source code remains subject to its included software license.",
    sections: [
      { title: "Using Kinu", body: <p>You may use Kinu to manage credentials for accounts and systems you are authorized to control. By using the hosted service, you agree to these terms. Keep your account details accurate and protect your sign-in credentials and connected provider accounts.</p> },
      { title: "Actions you authorize", body: <>
        <p>When you connect an account and configure a managed credential, you authorize Kinu to perform the actions permitted by that connection and your rotation controls. These may include creating replacement keys, updating application variables, deploying applications, checking health, and revoking predecessor keys.</p>
        <p>You are responsible for selecting the right accounts, projects, variables, health checks, and approval settings. Keep independent recovery access to your providers. Rotation changes live authentication and can interrupt applications, particularly when a consumer using the key has not been registered.</p>
      </> },
      { title: "Acceptable use", body: <p>Do not use Kinu to access systems without permission, obtain another person’s credentials, bypass provider restrictions, or disrupt the service. Do not submit material you are not entitled to process. We may restrict access to address abuse, a compromised account, or a threat to the service.</p> },
      { title: "Connected services", body: <p>Vercel, Railway, Builder.io, key providers, and other connected services operate under their own terms and account limits. Their fees, availability, permissions, and API behavior can affect Kinu’s operation. You remain responsible for those accounts and any charges they incur.</p> },
      { title: "Availability", body: <p>Kinu is provided on an as-available basis. We do not guarantee uninterrupted access or that every rotation will succeed. Review failed or held operations before retrying them, and maintain your own recovery arrangements. Nothing in these terms excludes rights or liability that applicable law does not allow to be excluded.</p> },
      { title: "Ending use and contacting us", body: <p>You can stop using Kinu by disabling rotation controls and disconnecting your accounts. This does not reverse completed actions or remove records automatically. Contact {support} for account closure, data requests, or questions about these terms. Our <a href="/privacy">privacy policy</a> explains how the hosted service handles information.</p> },
    ],
  },
  privacy: {
    title: "Privacy policy.",
    introduction: "This notice explains how Kinu handles information in the hosted service at kinu.web.app. Contact support.kinu@muwa.io with privacy questions or requests.",
    sections: [
      { title: "Information Kinu processes", body: <ul>
        <li><strong>Account information:</strong> your name, email address, password hash, workspace membership, and access role.</li>
        <li><strong>Connection information:</strong> provider and project identifiers, authorization tokens, selected variables, credential metadata, and encrypted key versions.</li>
        <li><strong>Operational records:</strong> configured alert content, incidents, rotation steps, approvals, audit activity, and redacted browser evidence.</li>
        <li><strong>Security information:</strong> session and form tokens, request metadata, and rate-limit records used to protect accounts and diagnose failures.</li>
      </ul> },
      { title: "How it is used", body: <p>Kinu uses this information to authenticate users, enforce workspace permissions, operate the connections and rotations you configure, verify outcomes, provide an audit history, send account and operational emails, and protect the service. Providing account and connection information is necessary for the corresponding features to work.</p> },
      { title: "Secrets and AI", body: <p>Managed key values and provider tokens are encrypted in Kinu’s private storage. Passwords are hashed. The private executor handles secret values and provider login state; the AI receives sanitized incident or action context. Raw managed keys, provider login state, and browser screenshots are not sent to the AI model.</p> },
      { title: "Service providers and connected accounts", body: <>
        <p>Firebase Hosting serves the frontend, Resend delivers account and notification emails, and Builder.io processes AI requests when connected. Kinu’s backend and private storage run on its hosting infrastructure. Your chosen providers receive the requests necessary to operate the connections you authorize.</p>
        <p>Workspace members can access records according to their permissions. Service providers may process information in countries outside your own. Their processing is governed by their own agreements and privacy notices. Kinu does not sell personal information or use managed key values for advertising.</p>
      </> },
      { title: "Cookies and retention", body: <>
        <p>Kinu uses session and form cookies to keep you signed in and protect requests. Hosted sign-in sessions last up to 12 hours; email verification and reset codes expire after 10 minutes. These cookies serve authentication and security purposes.</p>
        <p>Redacted browser evidence expires after 90 days. Account, connection, and operational records are retained while needed to operate the workspace, maintain its audit history, resolve failures, and protect the service. Disconnecting a provider stops future authorized use of that connection; it does not automatically erase historical records.</p>
      </> },
      { title: "Your choices and requests", body: <p>You can change connection permissions, disconnect providers, and manage workspace access. Depending on applicable law, you may request access, correction, export, deletion, restriction, or objection to processing, and raise a concern with your local data-protection authority. Contact {support}; we may need to verify your identity and workspace authority before acting.</p> },
      { title: "Self-hosted installations", body: <p>If you run Kinu yourself, you control that installation’s hosting, configuration, and stored data. The installation operator is responsible for its privacy practices. Connected providers still handle requests made to their services under their own terms.</p> },
    ],
  },
}

export default function PublicInfoPage({ page }: { page: Page }) {
  const article = articles[page]
  useEffect(() => {
    document.title = `${article.title.replace(/\.$/, "")} · Kinu`
    document.querySelector('meta[name="description"]')?.setAttribute("content", article.introduction)
  }, [article])

  return <div className="public-info">
    <a className="public-info__skip" href="#content">Skip to content</a>
    <header className="public-info__header">
      <a href="/" aria-label="Kinu home"><img src={kinuLogo} alt="Kinu" width="78" height="32" /></a>
      <nav aria-label="Public pages">
        {links.map(link => <a key={link.page} href={link.href} aria-current={page === link.page ? "page" : undefined}>{link.label}</a>)}
        <a href="/auth">Sign in <ArrowUpRight size={14} aria-hidden="true" /></a>
      </nav>
    </header>
    <main id="content" tabIndex={-1} className="public-info__article">
      <header>
        <h1>{article.title}</h1>
        <p className="public-info__introduction">{article.introduction}</p>
      </header>
      {article.sections.map(section => <section key={section.title}>
        <h2>{section.title}</h2>
        {section.body}
      </section>)}
    </main>
    <footer className="public-info__footer"><a href="/">Kinu</a><span>Questions? {support}</span></footer>
  </div>
}
