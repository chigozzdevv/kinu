import { useState, type FormEvent } from "react"
import { Eye, EyeOff, LoaderCircle } from "lucide-react"
import kinuLogo from "../assets/kinu-logo.svg"
import { Button } from "../components/ui/button"
import { clearSession, submitAuth } from "../lib/auth"

type Mode = "signin" | "signup" | "verify" | "forgot" | "reset"
const titles: Record<Mode, string> = {
  signin: "Sign in", signup: "Create account", verify: "Verify email",
  forgot: "Forgot password", reset: "Reset password",
}
const descriptions: Record<Mode, string> = {
  signin: "Sign in to your Kinu workspace.",
  signup: "Create your account to get started with Kinu.",
  verify: "Enter the six-digit verification code sent to your email.",
  forgot: "Enter your email to request a password reset code.",
  reset: "Enter your reset code and choose a new password.",
}
const actions: Record<Mode, string> = {
  signin: "Sign in", signup: "Create account", verify: "Verify email",
  forgot: "Send reset code", reset: "Update password",
}
const inputClass = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[13px] text-[var(--ink)] placeholder:text-[var(--ink-muted)] disabled:opacity-50"
const linkClass = "focus-ring rounded text-[12px] font-medium text-[var(--ink-soft)] underline-offset-4 hover:text-[var(--ink)] hover:underline disabled:opacity-50"

export function SignInPage({ configured, returnTo }: { configured: boolean; returnTo: string }) {
  const [mode, setMode] = useState<Mode>("signin")
  const [email, setEmail] = useState("")
  const [name, setName] = useState("")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [code, setCode] = useState("")
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [message, setMessage] = useState("")
  const newPassword = mode === "signup" || mode === "reset"
  const needsPassword = mode === "signin" || newPassword
  const needsCode = mode === "verify" || mode === "reset"
  const disabled = !configured || busy

  function change(next: Mode, notice = "") {
    setMode(next)
    setPassword("")
    setConfirm("")
    setCode("")
    setVisible(false)
    setError("")
    setMessage(notice)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (disabled) return
    setError("")
    setMessage("")
    if (newPassword && password !== confirm) { setError("Passwords do not match."); return }
    setBusy(true)
    try {
      const body: Record<string, string> = { email: email.trim() }
      if (needsPassword) body.password = password
      if (needsCode) body.code = code
      if (mode === "signup") body.name = name.trim()
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
        <a href="/" className="focus-ring mx-auto block w-fit rounded" aria-label="Kinu home"><img className="h-auto w-[116px]" src={kinuLogo} alt="Kinu" /></a>
        <h1 id="auth-title" className="mt-8 text-center text-[22px] font-semibold tracking-[-0.035em] text-[var(--ink)]">{titles[mode]}</h1>
        <p className="mt-2 text-center text-[13px] leading-6 text-[var(--ink-soft)]">{descriptions[mode]}</p>
        <form className="mt-7 space-y-4" onSubmit={(event) => { void submit(event) }}>
          {mode === "signup" && <label className="block space-y-2 text-[12px] font-medium text-[var(--ink)]" htmlFor="auth-name"><span>Name</span><input id="auth-name" name="name" className={inputClass} autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} maxLength={80} required disabled={disabled} /></label>}
          <label className="block space-y-2 text-[12px] font-medium text-[var(--ink)]" htmlFor="auth-email"><span>Email</span><input id="auth-email" name="email" className={inputClass} type="email" autoComplete="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} maxLength={254} required disabled={disabled} /></label>
          {needsCode && <label className="block space-y-2 text-[12px] font-medium text-[var(--ink)]" htmlFor="auth-code"><span>{mode === "reset" ? "Reset code" : "Verification code"}</span><input id="auth-code" name="code" className={inputClass} autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" placeholder="123456" value={code} onChange={(event) => setCode(event.target.value.replace(/[^0-9]/g, "").slice(0, 6))} maxLength={12} required disabled={disabled} /></label>}
          {needsPassword && <div>
            <label className="block space-y-2 text-[12px] font-medium text-[var(--ink)]" htmlFor="auth-password"><span>{mode === "reset" ? "New password" : "Password"}</span><div className="relative"><input id="auth-password" name="password" className={`${inputClass} pr-11`} type={visible ? "text" : "password"} autoComplete={newPassword ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={newPassword ? 12 : 1} maxLength={256} required disabled={disabled} aria-describedby={newPassword ? "password-help" : undefined} /><button className="focus-ring absolute inset-y-0 right-0 grid w-11 place-items-center rounded-xl text-[var(--ink-muted)]" type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "Hide password" : "Show password"} disabled={disabled}>{visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</button></div></label>
            {newPassword && <p id="password-help" className="mt-2 text-[11px] leading-5 text-[var(--ink-muted)]">At least 12 characters, with uppercase, lowercase, a number and a symbol.</p>}
          </div>}
          {newPassword && <label className="block space-y-2 text-[12px] font-medium text-[var(--ink)]" htmlFor="auth-confirm"><span>Confirm password</span><input id="auth-confirm" name="confirm" className={inputClass} type={visible ? "text" : "password"} autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} minLength={12} maxLength={256} required disabled={disabled} /></label>}
          {mode === "signin" && <div className="text-right"><button type="button" className={linkClass} disabled={disabled} onClick={() => change("forgot")}>Forgot password?</button></div>}
          {error && <p role="alert" className="text-[12px] leading-5 text-[var(--red)]">{error}</p>}
          {message && <p role="status" className="text-[12px] leading-5 text-[var(--ink-soft)]">{message}</p>}
          {!configured && <p role="alert" className="text-[12px] text-[var(--red)]">Sign-in is not configured.</p>}
          <Button className="h-11 w-full text-[13px]" type="submit" disabled={disabled}>{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}{busy ? "Please wait…" : actions[mode]}</Button>
        </form>
        <div className="mt-6 space-y-3 text-center text-[12px] text-[var(--ink-soft)]">
          {mode === "signin" ? <p>New to Kinu? <button type="button" className={linkClass} disabled={disabled} onClick={() => change("signup")}>Create account</button></p> : <button type="button" className={linkClass} disabled={disabled} onClick={() => change("signin")}>Back to sign in</button>}
          {mode === "verify" && <div><button type="button" className={linkClass} disabled={disabled} onClick={() => { void resend() }}>Send another code</button></div>}
        </div>
      </section>
    </main>
  )
}
