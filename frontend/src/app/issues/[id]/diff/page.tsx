import Link from 'next/link'
import { getWorkflow, type Workflow } from '@/lib/api'
import { diffStats, parseDiff } from '@/lib/diff'
import { ErrorPanel } from '@/components/error-panel'

export const dynamic = 'force-dynamic'

export default async function DiffPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params

  let workflow: Workflow | null = null
  let error: string | null = null
  try {
    workflow = await getWorkflow(id)
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause)
  }

  if (error || !workflow) {
    return (
      <main className="p-6 max-w-7xl mx-auto">
        <ErrorPanel
          title={error ? 'Backend unavailable' : 'No run yet'}
          detail={error ?? `Issue '${id}' has not been run.`}
        />
      </main>
    )
  }

  // The patch that was validated, or the most recent one attempted.
  const attempt =
    workflow.attempts.find((a) => a.status === 'passed') ??
    [...workflow.attempts].reverse().find((a) => a.diff)
  const files = parseDiff(attempt?.diff)
  const stats = diffStats(files)

  return (
    <main className="p-6 max-w-7xl mx-auto">
      <div className="mb-6 text-sm text-on-surface-variant flex items-center gap-2">
        <Link href="/" className="hover:text-primary transition-colors">Issues</Link>
        <span className="material-symbols-outlined text-[16px]">chevron_right</span>
        <Link href={`/issues/${id}`} className="hover:text-primary transition-colors">
          {workflow.repo}
        </Link>
        <span className="material-symbols-outlined text-[16px]">chevron_right</span>
        <span className="text-on-surface">Diff</span>
      </div>

      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-on-surface mb-2">Generated Patch</h1>
        <p className="text-sm text-on-surface-variant">
          {files.length
            ? `${stats.files} file${stats.files === 1 ? '' : 's'} changed, ${stats.added} added, ${stats.removed} removed`
            : 'No patch was produced for this issue.'}
          {attempt && ` · attempt ${attempt.attempt_number} (${attempt.status})`}
        </p>
      </header>

      <div className="space-y-6">
        {files.map((file) => (
          <div key={file.path} className="bg-surface-container-lowest border border-outline-variant rounded overflow-hidden">
            <div className="px-4 py-2 bg-surface-container-low border-b border-outline-variant font-mono text-sm text-on-surface flex justify-between">
              <span>{file.path}</span>
              <span className="text-xs">
                <span className="text-success">+{file.added}</span>{' '}
                <span className="text-error">−{file.removed}</span>
              </span>
            </div>
            <div className="overflow-x-auto">
              {file.hunks.map((hunk) => (
                <div key={hunk.header}>
                  <div className="px-4 py-1 bg-surface-container text-on-surface-variant font-mono text-xs opacity-75">
                    {hunk.header}
                  </div>
                  <div className="font-mono text-sm">
                    {hunk.lines.map((line, index) => (
                      <div
                        key={index}
                        className={`flex ${
                          line.type === 'added'
                            ? 'bg-[rgba(74,222,128,0.1)] text-success'
                            : line.type === 'removed'
                              ? 'bg-[rgba(255,180,171,0.1)] text-error'
                              : 'text-on-surface'
                        }`}
                      >
                        <div className="w-12 shrink-0 text-right pr-4 text-xs text-on-surface-variant opacity-50 select-none py-0.5 border-r border-outline-variant mr-4">
                          {line.newLine ?? line.oldLine ?? ''}
                        </div>
                        <div className="py-0.5 whitespace-pre pr-4">
                          {line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '}
                          {line.content}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}

        {!files.length && (
          <div className="bg-surface-container border border-outline-variant rounded p-4 text-xs text-on-surface-variant">
            {workflow.error ?? 'No candidate produced an appliable patch.'}
          </div>
        )}
      </div>
    </main>
  )
}
