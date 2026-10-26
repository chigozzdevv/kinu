import { useState } from "react"
import { ShieldCheck } from "lucide-react"
import kinuLogo from "../assets/kinu-logo.svg"
import { Button } from "../components/ui/button"
import { formControl } from "../components/workspace"
import {
  authenticationConfigured,
  createEmailAccount,
  EmailVerificationRequiredError,
  resetEmailPassword,
  signInWithEmail,
  signInWithSSO,
} from "../lib/auth"
import { dashboardLocation } from "../lib/callback"

type Mode = "sign-in" | "create" | "reset" | "email-sent"

function message(reason: unknown): string {
  if (reason instanceof EmailVerificationRequiredError) return reason.message
  const code = typeof reason === "object" && reason !== null && "code" in reason ? String(reason.code) : ""
  if (code === "auth/invalid-credential" || code === "auth/wrong-password" || code === "auth/user-not-found") return "Email or password is incorrect."
  if (code === "auth/email-already-in-use") return "An account already uses this email."
  if (code === "auth/invalid-email") return "Enter a valid email address."
  if (code === "auth/weak-password") return "Use a stronger password."
  if (code === "auth/too-many-requests") return "Too many attempts. Try again later."
  if (code === "auth/network-request-failed") return "Check your connection and try again."
  return "Authentication could not be completed. Try again."
}

export function SignInPage() {
  const [mode, setMode] = useState<Mode>("sign-in")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const changeMode = (next: Mode) => {
    setMode(next)
    setPassword("")
    setError("")
  }

  const handleEmail = async () => {
    setError("")
    setSubmitting(true)
    try {
      if (mode === "sign-in") {
        await signInWithEmail(email, password)
        window.location.replace(dashboardLocation())
      } else if (mode === "create") {
        await createEmailAccount(email, password)
        changeMode("email-sent")
      } else if (mode === "reset") {
        await resetEmailPassword(email)
        changeMode("email-sent")
      }
    } catch (reason) {
      setError(message(reason))
    } finally {
      setSubmitting(false)
    }
  }

  const handleSSO = async () => {
    setError("")
    setSubmitting(true)
    try {
      await signInWithSSO()
      window.location.replace(dashboardLocation())
    } catch (reason) {
      const code = typeof reason === "object" && reason !== null && "code" in reason ? String(reason.code) : ""
      if (code !== "auth/popup-closed-by-user" && code !== "auth/cancelled-popup-request") setError(message(reason))
    } finally {
      setSubmitting(false)
    }
  }

  const title = mode === "create" ? "Sign up" : mode === "reset" ? "Reset password" : mode === "email-sent" ? "Check your email" : "Sign in"
  const ready = authenticationConfigured && Boolean(email.trim()) && (mode === "reset" || Boolean(password))

  return (
    <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6 py-12">
      <section className="w-full max-w-[440px] rounded-[22px] border border-[var(--border)] bg-white px-8 py-10 shadow-[0_14px_40px_rgba(25,27,30,0.05)] sm:px-10">
        <h1 className="flex items-center justify-center gap-4 text-[20px] font-semibold tracking-[-0.035em] text-[var(--ink)]">
          <img className="h-auto w-[132px]" src={kinuLogo} alt="Kinu" />
          <span className="h-7 w-px bg-[var(--border)]" aria-hidden="true" />
          <span>{title}</span>
        </h1>
        {mode === "email-sent" ? (
          <div className="mt-8 text-center">
            <p className="text-[12px] leading-5 text-[var(--ink-soft)]">Follow the link sent to {email}.</p>
            <Button className="mt-6 h-12 w-full text-[14px]" onClick={() => changeMode("sign-in")}>Back to sign in</Button>
          </div>
        ) : (
          <>
            <form className="mt-8 space-y-4" onSubmit={(event) => { event.preventDefault(); void handleEmail() }}>
              <label className="block">
                <span className="mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]">Email</span>
                <input className={formControl} type="email" autoComplete="email" value={email} onChange={(event) => { setEmail(event.target.value); setError("") }} required />
              </label>
              {mode !== "reset" && <label className="block">
                <span className="mb-1.5 flex items-center justify-between text-[10px] font-semibold text-[var(--ink-soft)]">
                  <span>Password</span>
                  {mode === "sign-in" && <button className="focus-ring rounded-md text-[var(--ink-soft)] hover:text-[var(--ink)]" type="button" onClick={() => changeMode("reset")}>Forgot password?</button>}
                </span>
                <input className={formControl} type="password" autoComplete={mode === "create" ? "new-password" : "current-password"} minLength={mode === "create" ? 12 : undefined} value={password} onChange={(event) => { setPassword(event.target.value); setError("") }} required />
              </label>}
              <Button className="h-12 w-full text-[14px]" type="submit" disabled={submitting || !ready}>
                {submitting ? "Continuing…" : mode === "create" ? "Sign up" : mode === "reset" ? "Send reset link" : "Sign in"}
              </Button>
            </form>
            {mode !== "reset" && <>
              <div className="my-6 flex items-center gap-3 text-[9px] font-medium uppercase tracking-[0.08em] text-[var(--ink-muted)]"><span className="h-px flex-1 bg-[var(--border-soft)]" /><span>Or</span><span className="h-px flex-1 bg-[var(--border-soft)]" /></div>
              <Button className="h-12 w-full text-[14px]" variant="secondary" disabled={submitting || !authenticationConfigured} onClick={() => { void handleSSO() }}>
                <ShieldCheck className="size-5" />
                Continue with SSO
              </Button>
            </>}
            <div className="mt-6 text-center text-[11px] text-[var(--ink-soft)]">
              {mode === "sign-in" ? <>New to Kinu? <button className="focus-ring rounded-md font-semibold text-[var(--ink)]" type="button" onClick={() => changeMode("create")}>Sign up</button></> : <button className="focus-ring rounded-md font-semibold text-[var(--ink)]" type="button" onClick={() => changeMode("sign-in")}>Back to sign in</button>}
            </div>
          </>
        )}
        {!authenticationConfigured ? <p className="mt-4 text-center text-[12px] text-[var(--red)]">Sign-in is not configured.</p> : null}
        {error ? <p className="mt-4 text-center text-[12px] text-[var(--red)]">{error}</p> : null}
      </section>
    </main>
  )
}
