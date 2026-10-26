import { forwardRef, type ButtonHTMLAttributes } from "react"
import { cn } from "../../lib/utils"

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost"
  size?: "sm" | "md"
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", children, ...props }, ref) => {
    const variants = {
      primary: "border-[var(--accent)] bg-[var(--accent)] text-white hover:bg-[#111315]",
      secondary: "border-[var(--border)] bg-white text-[var(--ink)] hover:bg-[var(--surface-soft)]",
      danger: "border-[#dfb8bd] bg-[var(--red-soft)] text-[var(--red)] hover:bg-[#f2dadd]",
      ghost: "border-transparent bg-transparent text-[var(--ink-soft)] hover:bg-white/70 hover:text-[var(--ink)]",
    }
    const sizes = { sm: "h-8 px-3 text-[11px]", md: "h-10 px-4 text-[12px]" }

    return (
      <button
        ref={ref}
        className={cn(
          "focus-ring inline-flex items-center justify-center gap-2 rounded-xl border font-semibold transition disabled:pointer-events-none disabled:opacity-45",
          variants[variant],
          sizes[size],
          className,
        )}
        {...props}
      >
        {children}
      </button>
    )
  },
)

Button.displayName = "Button"
