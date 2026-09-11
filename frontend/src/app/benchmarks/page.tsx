import { API_BASE } from '@/lib/api'
import { ErrorPanel } from '@/components/error-panel'
import { MetricCard } from '@/components/metric-card'

export const dynamic = 'force-dynamic'

interface EvaluationIssue {
  issue_id: string
  repo: string
  outcome: string
  failure_category: string | null
  attempts: number
  top1_hit: boolean | null
  top3_hit: boolean | null
  fix_file_top1: boolean | null
  fix_file_top3: boolean | null
  generation_ms: number
  validation_ms: number
  wall_ms: number
  pr_created: boolean
}

interface EvaluationSummary {
  model: string
  embedding_model: string
  candidate_limit: number
  issues_attempted: number
  issues_scored: number
  environment_failures: number
  harness_errors: number
  validated_fixes: number
  success_rate: number
  localization_top1: number | null
  localization_top3: number | null
  fix_file_top1: number | null
  fix_file_top3: number | null
  total_candidates_generated: number
  avg_candidates_per_issue: number
  avg_generation_ms: number
  avg_validation_ms: number
  avg_wall_ms: number
  failure_categories: Record<string, number>
  prs_created: number
  generated_at: string
}

interface EvaluationReport {
  available: boolean
  summary: EvaluationSummary | null
  issues: EvaluationIssue[]
  model?: string
  generated_at?: string
}

const OUTCOME_STYLES: Record<string, string> = {
  validated_fix: 'bg-success-container text-success border-success-border',
  no_validated_fix: 'bg-error-container text-error border-error',
  environment_failure: 'bg-surface-container-low text-on-surface-variant border-surface-variant',
  error: 'bg-surface-container-low text-on-surface-variant border-surface-variant',
}

function seconds(ms: number): string {
  if (!ms) return '—'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

export default async function BenchmarksPage() {
  let report: EvaluationReport | null = null
  let error: string | null = null

  try {
    const response = await fetch(`${API_BASE}/api/benchmark/evaluation`, { cache: 'no-store' })
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
    report = (await response.json()) as EvaluationReport
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause)
  }

  const summary = report?.summary

  return (
    <main className="p-6 max-w-7xl mx-auto">
      <header className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-3xl font-semibold tracking-tight text-on-surface">Benchmark Evaluation</h1>
          {summary && (
            <span className="px-2 py-0.5 bg-secondary-container text-secondary-fixed-dim rounded text-[11px] font-bold tracking-[0.05em]">
              {summary.model}
            </span>
          )}
        </div>
        <p className="text-sm text-on-surface-variant">
          The complete system run against the validated benchmark set: localization, generation,
          sandbox validation and delivery.
        </p>
      </header>

      {error && <ErrorPanel title="Backend unavailable" detail={error} />}

      {report && !report.available && (
        <div className="bg-surface-container border border-outline-variant rounded p-6 text-sm text-on-surface-variant">
          No evaluation has been run yet. Produce one with:
          <pre className="mt-2 font-mono text-[11px] text-on-surface bg-surface-container-lowest border border-outline-variant rounded p-3">
            python -m backend.evaluation run
          </pre>
        </div>
      )}

      {summary && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-8">
            <MetricCard label="Issues Scored" value={summary.issues_scored} />
            <MetricCard label="Validated Fixes" value={summary.validated_fixes} valueColor="text-primary" />
            <MetricCard label="Success Rate" value={percent(summary.success_rate)} valueColor="text-primary" />
            <MetricCard label="Fix File Top-1" value={percent(summary.fix_file_top1)} />
            <MetricCard label="Fix File Top-3" value={percent(summary.fix_file_top3)} />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-8">
            <MetricCard label="Candidates" value={summary.total_candidates_generated} />
            <MetricCard label="Avg Candidates" value={summary.avg_candidates_per_issue} />
            <MetricCard label="Avg Generation" value={seconds(summary.avg_generation_ms)} />
            <MetricCard label="Avg Validation" value={seconds(summary.avg_validation_ms)} />
            <MetricCard label="Draft PRs" value={summary.prs_created} />
          </div>

          {/* Two localization metrics, because they answer different
              questions and only one of them is about this pipeline. */}
          <section className="bg-surface-container p-6 rounded border border-outline-variant mb-8">
            <h2 className="text-xl font-semibold tracking-tight mb-4">Localization</h2>
            <div className="grid md:grid-cols-2 gap-6 text-sm">
              <div>
                <div className="text-[11px] font-bold tracking-[0.05em] uppercase text-on-surface-variant mb-1">
                  Fix file found — Top-1 {percent(summary.fix_file_top1)} · Top-3 {percent(summary.fix_file_top3)}
                </div>
                <p className="text-on-surface-variant">
                  Whether the file the validated patch actually changed was among the ranked
                  candidates. This is the question that matters for fix generation.
                </p>
              </div>
              <div>
                <div className="text-[11px] font-bold tracking-[0.05em] uppercase text-on-surface-variant mb-1">
                  Regression test found — Top-1 {percent(summary.localization_top1)} · Top-3 {percent(summary.localization_top3)}
                </div>
                <p className="text-on-surface-variant">
                  The Phase 2 metric, whose ground truth is the location of the regression
                  test. The localizer ranks source files above test files, so this reads low
                  by design.
                </p>
              </div>
            </div>
          </section>

          {/* Environment failures are reported separately, never folded into
              the model's score. */}
          <section className="bg-surface-container p-6 rounded border border-outline-variant mb-8">
            <h2 className="text-xl font-semibold tracking-tight mb-4">Failure categories</h2>
            {Object.keys(summary.failure_categories).length ? (
              <div className="flex flex-wrap gap-2">
                {Object.entries(summary.failure_categories).map(([category, count]) => (
                  <span
                    key={category}
                    className="px-3 py-1 rounded border border-outline-variant bg-surface-container-low font-mono text-[12px] text-on-surface"
                  >
                    {category}: <span className="text-on-surface-variant">{count}</span>
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-on-surface-variant">No failures recorded.</p>
            )}
            <p className="text-xs text-on-surface-variant mt-4">
              {summary.environment_failures} environment failure
              {summary.environment_failures === 1 ? '' : 's'} and {summary.harness_errors} harness error
              {summary.harness_errors === 1 ? '' : 's'} are excluded from the success denominator —
              they measure the machine, not the model.
            </p>
          </section>

          <section className="bg-surface-container p-6 rounded border border-outline-variant">
            <h2 className="text-xl font-semibold tracking-tight mb-4">Per-issue results</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-outline-variant">
                    {['Issue', 'Repo', 'Outcome', 'Failure', 'Attempts', 'Fix file T1', 'Fix file T3', 'Generation', 'Validation', 'Wall'].map((h) => (
                      <th key={h} className="py-2 px-3 text-[11px] font-bold tracking-[0.05em] uppercase text-on-surface-variant">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="font-mono text-[12px]">
                  {report!.issues.map((issue) => (
                    <tr key={issue.issue_id} className="border-b border-surface-container-high hover:bg-surface-container-highest transition-colors">
                      <td className="py-2 px-3">{issue.issue_id}</td>
                      <td className="py-2 px-3 text-on-surface-variant">{issue.repo}</td>
                      <td className="py-2 px-3">
                        <span className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase ${OUTCOME_STYLES[issue.outcome] ?? ''}`}>
                          {issue.outcome.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-on-surface-variant">{issue.failure_category ?? '—'}</td>
                      <td className="py-2 px-3">{issue.attempts}</td>
                      <td className="py-2 px-3">{issue.fix_file_top1 === null ? '—' : issue.fix_file_top1 ? '✓' : '✗'}</td>
                      <td className="py-2 px-3">{issue.fix_file_top3 === null ? '—' : issue.fix_file_top3 ? '✓' : '✗'}</td>
                      <td className="py-2 px-3">{seconds(issue.generation_ms)}</td>
                      <td className="py-2 px-3">{seconds(issue.validation_ms)}</td>
                      <td className="py-2 px-3">{seconds(issue.wall_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-on-surface-variant mt-4">
              Generated {new Date(summary.generated_at).toLocaleString()} · candidate limit{' '}
              {summary.candidate_limit} · embeddings {summary.embedding_model}
            </p>
          </section>
        </>
      )}
    </main>
  )
}
