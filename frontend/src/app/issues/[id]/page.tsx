import Link from 'next/link'
import { getWorkflow, listWorkflows, type Workflow, type WorkflowSummary } from '@/lib/api'
import { ErrorPanel } from '@/components/error-panel'
import { RunWorkflowButton } from '@/components/run-workflow-button'
import { StatusChip } from '@/components/status-chip'

export const dynamic = 'force-dynamic'

export default async function IssueDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params

  let workflow: Workflow | null = null
  let summary: WorkflowSummary | undefined
  let error: string | null = null

  try {
    workflow = await getWorkflow(id)
    // The issue exists in the benchmark even when it has never been run, so
    // the page still has something to show.
    summary = (await listWorkflows()).issues.find((i) => i.issue_id === id)
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause)
  }

  if (error) {
    return (
      <main className="p-6 max-w-6xl mx-auto">
        <ErrorPanel title="Backend unavailable" detail={error} />
      </main>
    )
  }

  if (!workflow && !summary) {
    return (
      <main className="p-6 max-w-6xl mx-auto">
        <ErrorPanel title="Unknown issue" detail={`No benchmark issue '${id}'.`} />
      </main>
    )
  }

  const repo = workflow?.repo ?? summary!.repo
  const title = workflow?.title ?? summary!.title
  const validation = workflow?.benchmark_validation ?? summary!.benchmark_validation

  return (
    <main className="p-6 max-w-6xl mx-auto">
      <div className="space-y-6">
        <nav className="flex items-center gap-2 text-on-surface-variant text-xs">
          <Link href="/" className="hover:text-primary transition-colors">Issues</Link>
          <span className="material-symbols-outlined text-[14px]">chevron_right</span>
          <span>{repo}</span>
          <span className="material-symbols-outlined text-[14px]">chevron_right</span>
          <span className="text-on-surface font-mono text-[11px]">{id}</span>
        </nav>

        <div className="flex flex-col md:flex-row justify-between items-start gap-4">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-on-surface mb-2">{title}</h1>
            <div className="flex flex-wrap items-center gap-4 text-on-surface-variant text-xs">
              <div className="flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[16px]">book</span>
                Repo:
                <span className="font-mono text-[11px] text-on-surface bg-surface-container px-1.5 py-0.5 border border-outline-variant rounded">
                  {repo}
                </span>
              </div>
              {workflow && (
                <div className="flex items-center gap-1.5">
                  <span className="material-symbols-outlined text-[16px]">commit</span>
                  Base:
                  <span className="font-mono text-[11px] text-on-surface">
                    {workflow.base_commit.slice(0, 10)}
                  </span>
                </div>
              )}
              <div className="flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[16px]">fact_check</span>
                Benchmark validation: <span className="text-on-surface">{validation}</span>
              </div>
              {(workflow?.issue_url ?? summary?.issue_url) && (
                <a
                  href={workflow?.issue_url ?? summary!.issue_url}
                  className="flex items-center gap-1.5 hover:text-primary transition-colors"
                  target="_blank"
                  rel="noreferrer"
                >
                  <span className="material-symbols-outlined text-[16px]">open_in_new</span>
                  Upstream issue
                </a>
              )}
            </div>
          </div>

          <div className="flex flex-col items-end gap-3 shrink-0">
            {workflow && <StatusChip status={workflow.status === 'completed' ? 'verified' : workflow.status === 'failed' ? 'failed' : 'running'} />}
            <div className="flex gap-2 items-center">
              {workflow && (
                <>
                  <Link
                    href={`/issues/${id}/result`}
                    className="px-3 py-1.5 bg-surface-container border border-outline-variant rounded text-[11px] font-bold tracking-[0.05em] uppercase hover:bg-surface-container-high transition-colors"
                  >
                    Result
                  </Link>
                  <Link
                    href={`/issues/${id}/diff`}
                    className="px-3 py-1.5 bg-surface-container border border-outline-variant rounded text-[11px] font-bold tracking-[0.05em] uppercase hover:bg-surface-container-high transition-colors"
                  >
                    Diff
                  </Link>
                  <Link
                    href={`/issues/${id}/pr`}
                    className="px-3 py-1.5 bg-surface-container border border-outline-variant rounded text-[11px] font-bold tracking-[0.05em] uppercase hover:bg-surface-container-high transition-colors"
                  >
                    PR
                  </Link>
                </>
              )}
              <RunWorkflowButton issueId={id} label={workflow ? 'Re-run' : 'Run Workflow'} />
            </div>
          </div>
        </div>

        {workflow?.error && (
          <ErrorPanel title={`Run ${workflow.status}`} detail={workflow.error} />
        )}

        {/* The issue text itself, as the benchmark records it. */}
        <section className="bg-surface-container border border-outline-variant rounded overflow-hidden">
          <div className="px-4 py-3 border-b border-outline-variant">
            <h2 className="text-xl font-semibold tracking-tight">Issue</h2>
          </div>
          <pre className="p-4 text-xs text-on-surface whitespace-pre-wrap font-mono leading-relaxed max-h-[32rem] overflow-y-auto">
            {workflow?.problem?.trim() || 'No issue description recorded for this benchmark entry.'}
          </pre>
        </section>

        {/* Phase 2 output. */}
        <section className="bg-surface-container border border-outline-variant rounded overflow-hidden">
          <div className="px-4 py-3 border-b border-outline-variant flex items-center justify-between">
            <h2 className="text-xl font-semibold tracking-tight">Localization</h2>
            <span className="text-[11px] text-on-surface-variant uppercase tracking-[0.05em]">
              Ranked candidate locations
            </span>
          </div>
          {workflow?.localization.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-outline-variant">
                    {['#', 'File', 'Symbol', 'Lines', 'Semantic', 'Lexical', 'Score'].map((h) => (
                      <th key={h} className="py-2 px-3 text-[11px] font-bold tracking-[0.05em] uppercase text-on-surface-variant">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="font-mono text-[12px]">
                  {workflow.localization.map((c) => (
                    <tr key={`${c.rank}-${c.file}-${c.symbol}`} className="border-b border-surface-container-high">
                      <td className="py-2 px-3 text-on-surface-variant">{c.rank}</td>
                      <td className="py-2 px-3">{c.file}</td>
                      <td className="py-2 px-3">
                        {c.symbol} <span className="text-on-surface-variant">({c.symbol_type})</span>
                      </td>
                      <td className="py-2 px-3 text-on-surface-variant">{c.start_line}–{c.end_line}</td>
                      <td className="py-2 px-3">{c.semantic_score.toFixed(3)}</td>
                      <td className="py-2 px-3">{c.lexical_score.toFixed(3)}</td>
                      <td className="py-2 px-3 text-primary font-bold">{c.final_score.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="p-4 text-xs text-on-surface-variant">
              No localization yet. Run the workflow to produce candidate locations.
            </p>
          )}
        </section>
      </div>
    </main>
  )
}
