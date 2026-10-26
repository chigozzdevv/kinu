import type { LucideIcon } from "lucide-react"

const tones = {
  accent: "bg-[var(--accent-soft)] text-[var(--accent)]",
  blue: "bg-[#dfeaf8] text-[#2776bd]",
  green: "bg-[var(--green-soft)] text-[var(--green)]",
  red: "bg-[var(--red-soft)] text-[var(--red)]",
  neutral: "bg-[#e8e8e7] text-[var(--ink-soft)]",
}

export function Marker({ icon: Icon, tone = "accent" }: { icon: LucideIcon; tone?: keyof typeof tones }) {
  return (
    <span className={`grid size-8 shrink-0 place-items-center rounded-full ${tones[tone]}`}>
      <Icon className="size-4" strokeWidth={1.8} />
    </span>
  )
}
