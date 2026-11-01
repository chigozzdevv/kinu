import kinuLogo from "../assets/kinu-logo.svg"

export function AuthHeader({ title, id }: { title: string; id?: string }) {
  return <h1 id={id} className="flex items-center justify-center gap-4 text-[20px] font-semibold tracking-[-0.035em] text-[var(--ink)]">
    <img className="h-[46px] w-[80px] shrink-0 object-cover object-left" src={kinuLogo} alt="Kinu" />
    <span className="h-7 w-px shrink-0 bg-[var(--border)]" aria-hidden="true" />
    <span className="min-w-0">{title}</span>
  </h1>
}
