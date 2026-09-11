import Link from 'next/link'
import { formatDuration, getWorkflow, type Workflow } from '@/lib/api'
import { ErrorPanel } from '@/components/error-panel'

export const dynamic = 'force-dynamic'

const FAILURE_LABELS: Record<string, string> = {
  generation_error: 'Model output could not be turned into a usable patch',
  patch_application_error: 'Patch did not apply to the base commit',
  verification_failed: 'Tests did not pass with this patch',
  environment_error: 'The sandbox environment failed, not the patch',
  protected_test_modified: 'Patch modified a protected test file',
  timeout: 'The step exceeded its time limit',
}

export default async function VerifiedResultPage({ params }: { params: Promise<{ id: string }> }) {
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
      <main className="p-6 max-w-5xl mx-auto">
        <ErrorPanel
          title={error ? 'Backend unavailable' : 'No run yet'}
          detail={error ?? `Issue '${id}' has not been run.`}
        />
      </main>
    )
  }

  const succeeded = workflow.status === 'completed'
  const passing = workflow.attempts.find((a) => a.status === 'passed')
  const model = workflow.attempts[0]?.model ?? '—'

  return (
    <main className="p-6 max-w-5xl mx-auto space-y-6">
      <nav className="flex items-center gap-2 text-on-surface-variant text-xs">
        <Link href="/" className="hover:text-primary transition-colors">Issues</Link>
        <span className="material-symbols-outlined text-[14px]">chevron_right</span>
        <Link href={`/issues/${id}`} className="hover:text-primary transition-colors">
          {workflow.repo}
        </Link>
        <span className="material-symbols-outlined text-[14px]">chevron_right</span>
        <span className="text-on-surface">Result</span>
      </nav>

      <header className="flex justify-between items-end border-b border-outline-variant pb-6">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-3xl font-semibold tracking-tight text-on-surface">
              {succeeded ? 'Verified Result' : 'Run Result'}
            </h1>
            <span
              className={`px-2 py-0.5 rounded text-[11px] font-bold tracking-[0.05em] border ${
                succeeded
                  ? 'bg-success-container text-success border-success-border'
                  : 'bg-error-container text-error border-error'
              }`}
            >
              {succeeded ? 'SUCCESS' : workflow.status.toUpperCase()}
            </span>
          </div>
          <p className="text-sm text-on-surface-variant">
            {succeeded
              ? 'The agent produced a patch that passes the regression test in the sandbox.'
              : 'No candidate passed validation. Every attempt is recorded below.'}
          </p>
        </div>

        <div className="flex gap-2">
          {[
            { href: `/issues/${id}/diff`, icon: 'difference', label: 'View Diff' },
            { href: `/issues/${id}/pr`, icon: 'merge', label: 'View PR' },
            { href: `/issues/${id}/evidence`, icon: 'terminal', label: 'View Evidence' },
          ].map((action) => (
            <Link
              key={action.href}
              href={action.href}
              className="px-4 py-2 bg-surface-container border border-outline-variant rounded text-[11px] font-bold tracking-[0.05em] uppercase hover:bg-surface-container-high flex items-center gap-2 transition-colors"
            >
              <span className="material-symbols-outlined text-[18px]">{action.icon}</span>
              {action.label}
            </Link>
          ))}
        </div>
      </header>

      {workflow.error && <ErrorPanel title="Recorded failure" detail={workflow.error} />}

      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Model', value: model },
          { label: 'Attempts', value: String(workflow.attempts_taken) },
          { label: 'Runtime', value: formatDuration(workflow.started_at, workflow.completed_at) },
          { label: 'Stage', value: workflow.current_stage },
        ].map((s) => (
          <div key={s.label} className="bg-surface-container border border-outline-variant p-4 rounded">
            <div className="text-[11px] font-bold tracking-[0.05em] uppercase text-on-surface-variant mb-1">
              {s.label}
            </div>
            <div className="text-xl font-semibold tracking-tight text-on-surface truncate" title={s.value}>
              {s.value}
            </div>
          </div>
        ))}
      </section>

      {/* Every candidate, not only the one that worked. */}
      <section className="bg-surface-container rounded border border-outline-variant overflow-hidden">
        <div className="px-4 py-3 border-b border-outline-variant flex items-center justify-between">
          <h2 className="text-xl font-semibold tracking-tight">Candidate Attempts</h2>
          <span className="text-[11px] text-on-surface-variant uppercase tracking-[0.05em]">
            bounded at {workflow.attempts.length} candidate{workflow.attempts.length === 1 ? '' : 's'}
          </span>
        </div>
        <div className="divide-y divide-surface-container-high">
          {workflow.attempts.map((attempt) => (
            <div key={attempt.attempt_number} className="p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span
                    className={`material-symbols-outlined text-[20px] ${
                      attempt.status === 'passed' ? 'text-success' : 'text-error'
                    }`}
                  >
                    {attempt.status === 'passed' ? 'check_circle' : 'cancel'}
                  </span>
                  <div>
                    <div className="font-semibold text-sm text-on-surface">
                      Attempt {attempt.attempt_number}
                      {attempt.hypothesis ? ` — ${attempt.hypothesis}` : ''}
                    </div>
                    <div className="text-xs text-on-surface-variant">
                      {attempt.failure_type && attempt.failure_type !== 'none'
                        ? (FAILURE_LABELS[attempt.failure_type] ?? attempt.failure_type)
                        : 'Passed every check'}
                      {' · '}
                      {formatDuration(attempt.started_at, attempt.completed_at)}
                    </div>
                  </div>
                </div>
                <div
                  className={`px-2 py-1 rounded text-[10px] font-bold tracking-[0.05em] font-mono ${
                    attempt.status === 'passed'
                      ? 'bg-success-container text-success border border-success-border'
                      : 'bg-error-container text-error border border-error'
                  }`}
                >
                  {attempt.status.toUpperCase()}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                {attempt.verification_results.map((check) => (
                  <span
                    key={check.check_name}
                    title={`${check.command} (exit ${check.exit_code})`}
                    className={`px-2 py-0.5 rounded border text-[10px] font-mono ${
                      check.status === 'passed'
                        ? 'bg-success-container text-success border-success-border'
                        : 'bg-error-container text-error border-error'
                    }`}
                  >
                    {check.check_name}: {check.status}
                  </span>
                ))}
              </div>
            </div>
          ))}
          {!workflow.attempts.length && (
            <p className="p-4 text-xs text-on-surface-variant">
              The run did not reach fix generation.
            </p>
          )}
        </div>
      </section>

      {passing && (
        <section className="bg-surface-container rounded border border-outline-variant overflow-hidden">
          <div className="px-4 py-3 border-b border-outline-variant">
            <h2 className="text-xl font-semibold tracking-tight">Validation Result</h2>
          </div>
          <div className="p-4 text-sm text-on-surface-variant">
            The regression test failed at base commit{' '}
            <span className="font-mono text-[11px] text-on-surface">
              {workflow.base_commit.slice(0, 10)}
            </span>{' '}
            and passes with this patch; previously-passing tests still pass.
          </div>
        </section>
      )}
    </main>
  )
}
