import Link from 'next/link'
import { listWorkflows, runStatusLabel, type WorkflowSummary } from '@/lib/api'
import { ErrorPanel } from '@/components/error-panel'
import { StatusChip } from '@/components/status-chip'

export const dynamic = 'force-dynamic'

// The stages a run moves through, in order (backend/models/run.py RunStage).
const STAGES = [
  { key: 'intake', label: 'Intake', icon: 'input' },
  { key: 'localization', label: 'Localization', icon: 'location_searching' },
  { key: 'generation', label: 'Generation', icon: 'auto_awesome' },
  { key: 'verification', label: 'Verification', icon: 'fact_check' },
  { key: 'pr_creation', label: 'Delivery', icon: 'merge' },
  { key: 'completed', label: 'Completed', icon: 'check_circle' },
] as const

function stageIndex(stage: string | null): number {
  return STAGES.findIndex((s) => s.key === stage)
}

export default async function RunsPage() {
  let issues: WorkflowSummary[] = []
  let error: string | null = null
  try {
    issues = (await listWorkflows()).issues
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause)
  }

  const runs = issues.filter((i) => i.run_id)

  return (
    <main className="p-6 max-w-7xl mx-auto">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-on-surface mb-2">Runs</h1>
        <p className="text-sm text-on-surface-variant">
          Each run carries one issue through localization, generation, sandbox validation and delivery.
        </p>
      </header>

      {error && <ErrorPanel title="Backend unavailable" detail={error} />}

      {!error && !runs.length && (
        <div className="bg-surface-container border border-outline-variant rounded p-6 text-sm text-on-surface-variant">
          No runs yet. Start one from the{' '}
          <Link href="/" className="text-primary hover:underline">
            issue list
          </Link>
          .
        </div>
      )}

      <div className="space-y-4">
        {runs.map((run) => {
          const reached = stageIndex(run.current_stage)
          return (
            <section
              key={run.run_id}
              className="bg-surface-container border border-outline-variant rounded overflow-hidden"
            >
              <div className="px-4 py-3 border-b border-outline-variant flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-3">
                    <Link
                      href={`/issues/${run.issue_id}/result`}
                      className="font-semibold text-on-surface hover:text-primary transition-colors truncate"
                    >
                      {run.title}
                    </Link>
                    <StatusChip status={runStatusLabel(run)} />
                  </div>
                  <div className="text-[11px] font-mono text-on-surface-variant mt-1 truncate">
                    {run.repo} · {run.issue_id} · run {run.run_id?.slice(0, 8)}
                  </div>
                </div>
                <div className="text-right shrink-0 text-[11px] text-on-surface-variant">
                  <div>{run.attempts_taken ?? 0} attempt{run.attempts_taken === 1 ? '' : 's'}</div>
                  {run.completed_at && <div>{new Date(run.completed_at).toLocaleString()}</div>}
                </div>
              </div>

              {/* Stage progress: what the run reached, from persisted state. */}
              <div className="px-4 py-3 flex flex-wrap gap-2">
                {STAGES.map((stage, index) => {
                  const done = reached >= 0 && index <= reached
                  const failedHere = run.status === 'failed' && index === reached
                  return (
                    <div
                      key={stage.key}
                      className={`flex items-center gap-1.5 px-2 py-1 rounded border text-[10px] font-bold tracking-[0.05em] uppercase ${
                        failedHere
                          ? 'bg-error-container/20 text-error border-error'
                          : done
                            ? 'bg-secondary-container text-on-secondary-container border-outline-variant'
                            : 'bg-surface-container-low text-on-surface-variant border-surface-variant opacity-60'
                      }`}
                    >
                      <span className="material-symbols-outlined text-[14px]">{stage.icon}</span>
                      {stage.label}
                    </div>
                  )
                })}
              </div>

              {run.error && (
                <pre className="px-4 pb-4 font-mono text-[11px] text-error whitespace-pre-wrap break-words">
                  {run.error}
                </pre>
              )}

              <div className="px-4 py-2 border-t border-outline-variant flex items-center justify-between text-[11px]">
                <div className="flex gap-3">
                  <Link href={`/issues/${run.issue_id}`} className="text-secondary hover:text-primary transition-colors">
                    Issue
                  </Link>
                  <Link href={`/issues/${run.issue_id}/diff`} className="text-secondary hover:text-primary transition-colors">
                    Diff
                  </Link>
                  <Link href={`/issues/${run.issue_id}/evidence`} className="text-secondary hover:text-primary transition-colors">
                    Evidence
                  </Link>
                  <Link href={`/issues/${run.issue_id}/pr`} className="text-secondary hover:text-primary transition-colors">
                    PR
                  </Link>
                </div>
                {run.pr_url && (
                  <a href={run.pr_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                    {run.pr_url}
                  </a>
                )}
              </div>
            </section>
          )
        })}
      </div>
    </main>
  )
}
