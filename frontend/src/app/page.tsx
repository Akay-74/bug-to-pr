import Link from 'next/link'
import { listWorkflows, runStatusLabel, type WorkflowSummary } from '@/lib/api'
import { MetricCard } from '@/components/metric-card'
import { StatusChip } from '@/components/status-chip'
import { ErrorPanel } from '@/components/error-panel'
import { RunWorkflowButton } from '@/components/run-workflow-button'

export const dynamic = 'force-dynamic'

function validationChip(status: WorkflowSummary['benchmark_validation']) {
  const ok = status === 'valid'
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded border text-[10px] font-bold tracking-[0.05em] uppercase ${
        ok
          ? 'bg-success-container text-success border-success-border'
          : 'bg-surface-container-low text-on-surface-variant border-surface-variant'
      }`}
    >
      {status.replace('_', ' ')}
    </span>
  )
}

export default async function DashboardPage() {
  let issues: WorkflowSummary[] = []
  let error: string | null = null

  try {
    issues = (await listWorkflows()).issues
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause)
  }

  const withRuns = issues.filter((i) => i.run_id)
  const verified = issues.filter((i) => i.status === 'completed')
  const prs = issues.filter((i) => i.pr_url)
  const totalAttempts = withRuns.reduce((sum, i) => sum + (i.attempts_taken ?? 0), 0)
  const avgAttempts = withRuns.length ? (totalAttempts / withRuns.length).toFixed(1) : '—'

  return (
    <main className="p-6 max-w-7xl mx-auto">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-primary mb-2">
          Turn real GitHub bugs into verified fixes.
        </h1>
        <p className="text-sm text-on-surface-variant">
          Select a curated issue and inspect what the agent found, changed, and verified.
        </p>
      </header>

      {error && (
        <div className="mb-8">
          <ErrorPanel
            title="Backend unavailable"
            detail={error}
            hint="Start it with: uvicorn backend.api.main:app --reload"
          />
        </div>
      )}

      {/* Summary metrics, computed from real runs rather than declared. */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-8">
        <MetricCard label="Benchmark Issues" value={issues.length} />
        <MetricCard label="Runs Executed" value={withRuns.length} />
        <MetricCard label="Verified Fixes" value={verified.length} valueColor="text-primary" />
        <MetricCard label="Avg Attempts" value={avgAttempts} />
        <MetricCard
          label="Draft PRs"
          value={prs.length}
          valueColor="text-tertiary-fixed-dim"
          className="col-span-2 md:col-span-1"
        />
      </div>

      <section className="bg-surface-container p-6 rounded border border-outline-variant">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-semibold tracking-tight">Issue Selection</h2>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-outline-variant">
                {['Repository', 'Issue', 'Title', 'Benchmark', 'Stage', 'Status', 'Attempts', 'Actions'].map(
                  (heading) => (
                    <th
                      key={heading}
                      className={`py-2 px-3 text-[11px] font-bold tracking-[0.05em] uppercase text-on-surface-variant ${
                        heading === 'Actions' ? 'text-right' : ''
                      }`}
                    >
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="font-mono text-[13px]">
              {issues.map((issue) => (
                <tr
                  key={issue.issue_id}
                  className="border-b border-surface-container-high hover:bg-surface-container-highest transition-colors"
                >
                  <td className="py-3 px-3">{issue.repo}</td>
                  <td className="py-3 px-3 text-on-surface-variant text-[11px]">{issue.issue_id}</td>
                  <td className="py-3 px-3 font-sans text-xs truncate max-w-xs" title={issue.title}>
                    {issue.title}
                  </td>
                  <td className="py-3 px-3">{validationChip(issue.benchmark_validation)}</td>
                  <td className="py-3 px-3 text-on-surface-variant text-[11px]">
                    {issue.current_stage ?? '—'}
                  </td>
                  <td className="py-3 px-3">
                    {issue.run_id ? <StatusChip status={runStatusLabel(issue)} /> : <span className="text-on-surface-variant">—</span>}
                  </td>
                  <td className="py-3 px-3">{issue.attempts_taken ?? '—'}</td>
                  <td className="py-3 px-3 text-right">
                    <div className="flex justify-end gap-2 items-center">
                      {issue.run_id && (
                        <Link
                          href={`/issues/${issue.issue_id}/result`}
                          className="text-xs font-sans border border-outline-variant text-secondary px-3 py-1 rounded hover:bg-surface-container-highest transition-colors"
                        >
                          View Result
                        </Link>
                      )}
                      <Link
                        href={`/issues/${issue.issue_id}`}
                        className="text-xs font-sans border border-outline-variant text-secondary px-3 py-1 rounded hover:bg-surface-container-highest transition-colors"
                      >
                        View Issue
                      </Link>
                      <RunWorkflowButton issueId={issue.issue_id} />
                    </div>
                  </td>
                </tr>
              ))}
              {!issues.length && !error && (
                <tr>
                  <td colSpan={8} className="py-6 px-3 text-center text-on-surface-variant text-xs font-sans">
                    No benchmark issues found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-on-surface-variant">
          <div>
            Showing {issues.length} issue{issues.length === 1 ? '' : 's'} · {withRuns.length} with runs
          </div>
          <div className="flex gap-1 items-center bg-surface-container-low border border-surface-variant px-2 py-1 rounded">
            <span className="material-symbols-outlined text-[14px]">database</span>
            LIVE BACKEND STATE
          </div>
        </div>
      </section>
    </main>
  )
}
