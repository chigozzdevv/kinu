import { useState } from "react"
import { AuthHeader } from "../components/auth-header"
import { Button } from "../components/ui/button"
import { formControl } from "../components/workspace"
import { api } from "../lib/api"
import type { OrganisationMembership } from "../types"

export function OrganisationSetupPage({
  onCreated,
}: {
  onCreated: (membership: OrganisationMembership) => void
}) {
  const [name, setName] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const submit = async () => {
    setSubmitting(true)
    setError("")
    try {
      onCreated(await api.createOrganisation(name.trim()))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Organisation could not be created")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6 py-12">
      <section className="w-full max-w-[440px] rounded-[22px] border border-[var(--border)] bg-white px-8 py-10 shadow-[0_14px_40px_rgba(25,27,30,0.05)] sm:px-10">
        <AuthHeader title="Set up" />
        <form className="mt-8" onSubmit={(event) => { event.preventDefault(); void submit() }}>
          <label className="block">
            <span className="mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]">Organization name</span>
            <input className={formControl} autoComplete="organization" maxLength={120} value={name} onChange={(event) => { setName(event.target.value); setError("") }} required autoFocus />
          </label>
          <Button className="mt-4 h-12 w-full text-[14px]" type="submit" disabled={submitting || !name.trim()}>
            {submitting ? "Creating…" : "Continue"}
          </Button>
        </form>
        {error ? <p className="mt-4 text-center text-[12px] text-[var(--red)]">{error}</p> : null}
      </section>
    </main>
  )
}
