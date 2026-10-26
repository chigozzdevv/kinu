import { ShieldCheck } from "lucide-react"
import kinuLogo from "../assets/kinu-logo.svg"
import { Button } from "../components/ui/button"
import { signIn } from "../lib/auth"

export function SignInPage({ configured, returnTo }: { configured: boolean; returnTo: string }) {
  const failed = new URLSearchParams(window.location.search).get("error") === "signin"
  return (
    <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6 py-12">
      <section className="w-full max-w-[440px] rounded-[22px] border border-[var(--border)] bg-white px-8 py-10 shadow-[0_14px_40px_rgba(25,27,30,0.05)] sm:px-10">
        <h1 className="flex items-center justify-center gap-4 text-[20px] font-semibold tracking-[-0.035em] text-[var(--ink)]">
          <img className="h-auto w-[132px]" src={kinuLogo} alt="Kinu" />
          <span className="h-7 w-px bg-[var(--border)]" aria-hidden="true" />
          <span>Sign in</span>
        </h1>
        <p className="mt-7 text-center text-[13px] leading-6 text-[var(--ink-soft)]">
          Sign in to your Kinu workspace.
        </p>
        <Button className="mt-6 h-12 w-full text-[14px]" disabled={!configured} onClick={() => signIn(returnTo)}>
          <ShieldCheck className="size-5" />
          Continue to sign in
        </Button>
        {!configured && <p className="mt-4 text-center text-[12px] text-[var(--red)]">Sign-in is not configured.</p>}
        {failed && <p role="alert" className="mt-4 text-center text-[12px] text-[var(--red)]">Sign-in could not be completed. Please try again.</p>}
      </section>
    </main>
  )
}
