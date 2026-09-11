/**
 * How the dashboard shows a failure (docs/phase5.md: the UI must surface
 * errors). Used both for a backend that is unreachable and for a run that
 * failed with a recorded reason.
 */
export function ErrorPanel({
  title,
  detail,
  hint,
}: {
  title: string
  detail: string
  hint?: string
}) {
  return (
    <div className="bg-error-container/20 border border-error rounded p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="material-symbols-outlined text-error text-[20px]">error</span>
        <h2 className="text-sm font-semibold text-error">{title}</h2>
      </div>
      <pre className="font-mono text-[11px] text-on-surface whitespace-pre-wrap break-words">{detail}</pre>
      {hint && <p className="text-xs text-on-surface-variant mt-2">{hint}</p>}
    </div>
  )
}
