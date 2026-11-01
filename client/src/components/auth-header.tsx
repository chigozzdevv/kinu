import kinuLogo from "../assets/kinu-logo.svg"

export function AuthHeader({ title, id }: { title: string; id?: string }) {
  return <h1 id={id} className="flex items-center justify-center gap-3 text-[20px] font-semibold tracking-[-0.035em] text-[var(--ink)] sm:gap-4">
    <img className="h-8 w-auto max-w-[112px] shrink-0 object-contain sm:h-9" src={kinuLogo} alt="Kinu" />
    <span className="h-7 w-px shrink-0 bg-[var(--border)]" aria-hidden="true" />
    <span className="min-w-0">{title}</span>
  </h1>
}
