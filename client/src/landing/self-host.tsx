import { X } from "lucide-react"
import type { RefObject } from "react"
import "./self-host.css"

export default function SelfHost({ dialog }: { dialog: RefObject<HTMLDialogElement | null> }) {
  return (
    <dialog ref={dialog} className="new-self-host" aria-labelledby="new-self-host-title">
      <header className="new-self-host__header">
        <h2 id="new-self-host-title">Self-host Kinu</h2>
        <button
          type="button"
          className="new-self-host__close"
          aria-label="Close self-host instructions"
          onClick={() => dialog.current?.close()}
        >
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <p>Start from a Kinu source checkout on a host with Docker Compose, Python 3, and Make.</p>
      <ol>
        <li>
          <strong>Create your installation settings.</strong>
          <pre><code>python3 -m deploy.setup</code></pre>
          In <code>.env</code>, set <code>KINU_RESEND_KEY</code> and <code>KINU_EMAIL_SENDER</code>.
          Use a sender address from a domain verified in Resend.
        </li>
        <li>
          <strong>Start Kinu.</strong>
          <pre><code>{"make image\ndocker compose up -d --no-build"}</code></pre>
        </li>
        <li>
          <strong>Create your administrator account.</strong>
          <pre><code>python3 -m deploy.setup --link</code></pre>
          Open the private setup link it prints and verify your email.
          The local dashboard runs at <code>localhost:8080</code>.
        </li>
      </ol>
    </dialog>
  )
}
