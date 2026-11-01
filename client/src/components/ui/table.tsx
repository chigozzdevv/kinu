import { Children, type HTMLAttributes, type TableHTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from "react"
import { cn } from "../../lib/utils"

export function Table({ className, children, responsive = false, ...props }: TableHTMLAttributes<HTMLTableElement> & { responsive?: boolean | "compact" }) {
  return (
    <div className={responsive ? cn("resource-table-container", responsive === "compact" && "resource-table-compact") : "w-full overflow-x-auto"}>
      <table className={cn("w-full min-w-[720px] border-collapse text-left text-[12.5px]", responsive && "resource-table", className)} {...props}>{children}</table>
    </div>
  )
}

export function TableHeader({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("border-b border-[var(--border)] text-[var(--ink-soft)]", className)} {...props} />
}

export function TableBody({ className, children, columns = 1, emptyMessage = "No results.", ...props }: HTMLAttributes<HTMLTableSectionElement> & { columns?: number; emptyMessage?: string }) {
  return <tbody className={cn("divide-y divide-[var(--border-soft)]", className)} {...props}>{Children.toArray(children).length ? children : <tr data-empty><td colSpan={columns} className="py-10 text-center text-[11px] text-[var(--ink-muted)]">{emptyMessage}</td></tr>}</tbody>
}

export function TableRow({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("transition-colors", className)} {...props} />
}

export function TableHead({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("px-3 py-4 text-[12px] font-medium", className)} {...props} />
}

export function TableCell({ className, children, label, primary = false, action = false, wide = false, ...props }: TdHTMLAttributes<HTMLTableCellElement> & { label?: string; primary?: boolean; action?: boolean; wide?: boolean }) {
  return <td className={cn("px-3 py-[18px] text-[var(--ink)]", action && "text-right", className)} data-primary={primary || undefined} data-action={action || undefined} data-wide={wide || undefined} {...props}>{label && <span className="resource-cell-label" aria-hidden="true">{label}</span>}{children}</td>
}
