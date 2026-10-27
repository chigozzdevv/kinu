import { useState, type FormEvent } from "react"
import kinuLogo from "../assets/kinu-logo.svg"
import { Button } from "../components/ui/button"
import { formControl } from "../components/workspace"
import { clearSession, submitAuth } from "../lib/auth"

type Mode = "signin" | "signup" | "verify" | "forgot" | "reset"
const titles: Record<Mode, string> = {
  signin: "Sign in", signup: "Sign up", verify: "Verify email",
  forgot: "Reset password", reset: "Reset password",
}
const descriptions: Partial<Record<Mode, string>> = {
  verify: "Enter the six-digit verification code sent to your email.",
  forgot: "Enter your email to request a password reset code.",
  reset: "Enter your reset code and choose a new password.",
}
const actions: Record<Mode, string> = {
  signin: "Sign in", signup: "Sign up", verify: "Verify email",
  forgot: "Send reset code", reset: "Update password",
}
const labelClass = "mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]"
const linkClass = "focus-ring rounded-md font-semibold text-[var(--ink)] disabled:opacity-50"

export function SignInPage({ configured, returnTo }: { configured: boolean; returnTo: string }) {
  const [mode, setMode] = useState<Mode>("signin")
  const [email, setEmail] = useState("")
  const [name, setName] = useState("")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [code, setCode] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [message, setMessage] = useState(() => new URLSearchParams(window.location.search).get("expired") === "1" ? "Your session expired. Sign in to continue." : "")
  const newPassword = mode === "signup" || mode === "reset"
  const needsPassword = mode === "signin" || newPassword
  const needsCode = mode === "verify" || mode === "reset"
  const disabled = !configured || busy

  function change(next: Mode, notice = "") {
    setMode(next)
    setPassword("")
    setConfirm("")
    setCode("")
    setError("")
    setMessage(notice)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (disabled) return
    const fields = new FormData(event.currentTarget)
    const submittedEmail = String(fields.get("email") ?? "").trim()
    const submittedPassword = String(fields.get("password") ?? "")
    setEmail(submittedEmail)
    setError("")
    setMessage("")
    if (newPassword && submittedPassword !== fields.get("confirm")) { setError("Passwords do not match."); return }
    setBusy(true)
    try {
      const body: Record<string, string> = { email: submittedEmail }
      if (needsPassword) body.password = submittedPassword
      if (needsCode) body.code = String(fields.get("code") ?? "")
      if (mode === "signup") body.name = String(fields.get("name") ?? "").trim()
      if (mode === "signin") body.return_to = returnTo
      const result = await submitAuth(mode, body)
      if (result.next === "complete") {
        clearSession()
        window.location.replace(result.return_to ?? "/dashboard")
        return
      }
      const notice = mode === "reset" ? "Your password has been updated. Sign in to continue."
        : mode === "verify" ? "Email verified. Sign in to continue."
        : mode === "forgot" ? "If an account exists, a reset code is on its way."
        : result.next === "signin" ? "Your account is ready. Sign in to continue." : ""
      change(result.next, notice)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Something went wrong. Please try again.")
    } finally {
      setPassword("")
      setConfirm("")
      setBusy(false)
    }
  }

  async function resend() {
    if (disabled) return
    setBusy(true)
    setError("")
    setMessage("")
    try {
      await submitAuth("resend", { email: email.trim() })
      setMessage("If your account needs verification, a new code is on its way.")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The code could not be sent. Please try again.")
    } finally { setBusy(false) }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6 py-12">
      <section className="w-full max-w-[440px] rounded-[22px] border border-[var(--border)] bg-white px-8 py-10 shadow-[0_14px_40px_rgba(25,27,30,0.05)] sm:px-10" aria-labelledby="auth-title">
        <h1 id="auth-title" className="flex items-center justify-center gap-4 text-[20px] font-semibold tracking-[-0.035em] text-[var(--ink)]">
          <img className="h-[46px] w-[80px] shrink-0 object-cover object-left" src={kinuLogo} alt="Kinu" />
          <span className="h-7 w-px bg-[var(--border)]" aria-hidden="true" />
          <span>{titles[mode]}</span>
        </h1>
        {descriptions[mode] && <p className="mt-6 text-center text-[12px] leading-5 text-[var(--ink-soft)]">{descriptions[mode]}</p>}
        <form className="group mt-8 space-y-4" onSubmit={(event) => { void submit(event) }}>
          {mode === "signup" && <label className="block" htmlFor="auth-name">
            <span className={labelClass}>Name</span>
            <input id="auth-name" name="name" className={formControl} autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} maxLength={80} required disabled={disabled} />
          </label>}
          <label className="block" htmlFor="auth-email">
            <span className={labelClass}>Email</span>
            <input id="auth-email" name="email" className={formControl} type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} maxLength={254} required disabled={disabled} />
          </label>
          {needsCode && <label className="block" htmlFor="auth-code">
            <span className={labelClass}>{mode === "reset" ? "Reset code" : "Verification code"}</span>
            <input id="auth-code" name="code" className={formControl} autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value.replace(/[^0-9]/g, "").slice(0, 6))} maxLength={12} required disabled={disabled} />
          </label>}
          {needsPassword && <div>
            <div className="mb-1.5 flex items-center justify-between text-[10px] font-semibold text-[var(--ink-soft)]">
              <label htmlFor="auth-password">{mode === "reset" ? "New password" : "Password"}</label>
              {mode === "signin" && <button className="focus-ring rounded-md text-[var(--ink-soft)] hover:text-[var(--ink)]" type="button" disabled={disabled} onClick={() => change("forgot")}>Forgot password?</button>}
            </div>
            <input id="auth-password" name="password" className={formControl} type="password" autoComplete={newPassword ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={newPassword ? 12 : 1} maxLength={256} required disabled={disabled} aria-describedby={newPassword ? "password-help" : undefined} />
            {newPassword && <p id="password-help" className="mt-2 text-[11px] leading-5 text-[var(--ink-muted)]">At least 12 characters, with uppercase, lowercase, a number and a symbol.</p>}
          </div>}
          {newPassword && <label className="block" htmlFor="auth-confirm">
            <span className={labelClass}>Confirm password</span>
            <input id="auth-confirm" name="confirm" className={formControl} type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} minLength={12} maxLength={256} required disabled={disabled} />
          </label>}
          <Button className="h-12 w-full text-[14px] group-invalid:opacity-45" type="submit" disabled={disabled}>{busy ? "Continuing…" : actions[mode]}</Button>
        </form>
        <div className="mt-6 text-center text-[11px] text-[var(--ink-soft)]">
          {mode === "signin" ? <>New to Kinu? <button type="button" className={linkClass} disabled={disabled} onClick={() => change("signup")}>Sign up</button></> : <button type="button" className={linkClass} disabled={disabled} onClick={() => change("signin")}>Back to sign in</button>}
          {mode === "verify" && <div className="mt-3"><button type="button" className={linkClass} disabled={disabled} onClick={() => { void resend() }}>Send another code</button></div>}
        </div>
        {!configured && <p role="alert" className="mt-4 text-center text-[12px] text-[var(--red)]">Sign-in is not configured.</p>}
        {error && <p role="alert" className="mt-4 text-center text-[12px] leading-5 text-[var(--red)]">{error}</p>}
        {message && <p role="status" className="mt-4 text-center text-[12px] leading-5 text-[var(--ink-soft)]">{message}</p>}
      </section>
    </main>
  )
}
