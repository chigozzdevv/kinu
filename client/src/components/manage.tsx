import { useEffect, useState, type ReactNode } from "react"
import { Button } from "./ui/button"
import { Modal } from "./ui/modal"

export interface ResourceDependency {
  label: string
  items: string[]
}

export interface DeleteResourceDependency {
  label: string
  items: string[]
}

export function DeleteResourceModal({
  isOpen,
  onClose,
  resourceLabel,
  description,
  retainedResourceNote,
  dependencies = [],
  onDelete,
  deleting = false,
  error,
}: {
  isOpen: boolean
  onClose: () => void
  resourceLabel: string
  description?: string
  retainedResourceNote?: string
  dependencies?: DeleteResourceDependency[]
  onDelete: () => void
  deleting?: boolean
  error?: string
}) {
  const activeDependencies = dependencies.filter((dependency) => dependency.items.length > 0)

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Delete ${resourceLabel}?`}
      description={description}
      actions={<Button variant="danger" onClick={onDelete} disabled={deleting}>{deleting ? "Deleting…" : `Delete ${resourceLabel}`}</Button>}
    >
      <div className="space-y-5">
        {activeDependencies.length > 0 && (
          <div className="space-y-3">
            {activeDependencies.map((dependency) => (
              <div key={dependency.label} className="grid gap-1 sm:grid-cols-[9rem_1fr] sm:gap-4">
                <div className="text-[9px] font-semibold text-[var(--ink-muted)]">{dependency.label}</div>
                <div className="text-[10px] leading-4 text-[var(--ink)]">
                  {dependency.items.map((item) => (
                    <div key={item} className="font-medium">{item}</div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        {retainedResourceNote && <p className="text-[9px] leading-4 text-[var(--ink-muted)]">{retainedResourceNote}</p>}
        {error && <div role="alert" className="text-[10px] text-[var(--red)]">{error}</div>}
      </div>
    </Modal>
  )
}

export function ManageResourceModal({
  isOpen,
  onClose,
  title,
  resourceLabel,
  children,
  onSave,
  onDelete,
  dependencies = [],
  saveDisabled = false,
  saving = false,
  deleting = false,
  error,
  deleteTitle,
  deleteDescription,
  deleteTriggerLabel = "Delete",
  deleteActionLabel = "Delete",
  deletingActionLabel = "Deleting…",
}: {
  isOpen: boolean
  onClose: () => void
  title: string
  resourceLabel: string
  children: ReactNode
  onSave: () => void
  onDelete?: () => void
  dependencies?: ResourceDependency[]
  saveDisabled?: boolean
  saving?: boolean
  deleting?: boolean
  error?: string
  deleteTitle?: string
  deleteDescription?: string
  deleteTriggerLabel?: string
  deleteActionLabel?: string
  deletingActionLabel?: string
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const activeDependencies = dependencies.filter((dependency) => dependency.items.length > 0)

  useEffect(() => {
    if (!isOpen) setConfirmingDelete(false)
  }, [isOpen])

  const close = () => {
    if (confirmingDelete) setConfirmingDelete(false)
    else onClose()
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={close}
      title={confirmingDelete ? deleteTitle ?? `Delete ${resourceLabel}?` : title}
      description={confirmingDelete ? deleteDescription : undefined}
      cancelLabel={confirmingDelete ? "Back" : "Cancel"}
      footerStart={!confirmingDelete && onDelete ? <Button variant="ghost" onClick={() => setConfirmingDelete(true)}>{deleteTriggerLabel}</Button> : undefined}
      actions={confirmingDelete
        ? <Button variant="danger" onClick={onDelete} disabled={deleting}>{deleting ? deletingActionLabel : deleteActionLabel}</Button>
        : <Button onClick={onSave} disabled={saveDisabled || saving}>{saving ? "Saving…" : "Save changes"}</Button>}
    >
      {!confirmingDelete && children}
      {confirmingDelete && (
        activeDependencies.length > 0
          ? <div className="space-y-4">
              <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">
                {activeDependencies.map((dependency) => (
                  <div key={dependency.label} className="grid gap-2 py-3 sm:grid-cols-[8rem_1fr]">
                    <div className="text-[9px] font-semibold text-[var(--ink-muted)]">{dependency.label}</div>
                    <div className="text-[10px]">{dependency.items.join(", ")}</div>
                  </div>
                ))}
              </div>
            </div>
          : <p className="text-[10px] text-[var(--ink-soft)]">This removes the {resourceLabel} from Kinu.</p>
      )}
      {error && <div role="alert" className="mt-4 rounded-xl border border-[#ebcfd3] bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{error}</div>}
    </Modal>
  )
}
