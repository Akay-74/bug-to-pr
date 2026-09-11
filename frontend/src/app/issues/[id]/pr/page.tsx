import Link from 'next/link'
import { getWorkflow, shortSha, type Workflow } from '@/lib/api'
import { diffStats, parseDiff } from '@/lib/diff'
import { ErrorPanel } from '@/components/error-panel'

export const dynamic = 'force-dynamic'

const DELIVERY_LABELS: Record<string, string> = {
  pending: 'Not started',
  committed: 'Committed locally',
  pr_created: 'Draft PR open',
  failed: 'Delivery failed',
}

export default async function PRPage({ params }: { params: Promise<{ id: string }> }) {
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

  const delivery = workflow.delivery
  const attempt = workflow.attempts.find((a) => a.status === 'passed')
  const stats = diffStats(parseDiff(attempt?.diff))

  return (
    <main className="p-6 max-w-7xl mx-auto">
      <div className="mb-6 text-sm text-on-surface-variant flex items-center gap-2">
        <Link href="/" className="hover:text-primary transition-colors">Issues</Link>
        <span className="material-symbols-outlined text-[16px]">chevron_right</span>
        <Link href={`/issues/${id}`} className="hover:text-primary transition-colors">
          {workflow.repo}
        </Link>
        <span className="material-symbols-outlined text-[16px]">chevron_right</span>
        <span className="text-on-surface">Pull Request</span>
      </div>

      {!delivery ? (
        <ErrorPanel
          title="Nothing delivered yet"
          detail={
            workflow.status === 'completed'
              ? 'The fix validated but no branch or commit was created.'
              : (workflow.error ?? 'No validated fix, so nothing could be committed.')
          }
          hint="A PR is only ever created for a fix that passed validation."
        />
      ) : (
        <>
          <header className="mb-6">
            <h1 className="text-3xl font-semibold tracking-tight text-on-surface mb-3">
              {(delivery.commit_message ?? '').split('\n')[0] || workflow.title}
            </h1>
            <div className="flex flex-wrap items-center gap-3">
              <span
                className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider flex items-center gap-1 border ${
                  delivery.status === 'failed'
                    ? 'bg-error-container/20 text-error border-error/20'
                    : 'bg-[rgba(74,222,128,0.1)] text-success border-success/20'
                }`}
              >
                <span className="material-symbols-outlined text-[14px]">
                  {delivery.status === 'failed' ? 'error' : 'check_circle'}
                </span>
                {DELIVERY_LABELS[delivery.status] ?? delivery.status}
              </span>
              {delivery.is_draft && delivery.pr_url && (
                <span className="px-2.5 py-1 bg-surface-container text-on-surface-variant border border-outline-variant rounded-full text-xs font-bold uppercase tracking-wider">
                  Draft
                </span>
              )}
              <span className="px-2.5 py-1 bg-surface-container text-on-surface-variant border border-outline-variant rounded-full text-xs font-bold uppercase tracking-wider">
                {stats.files} file{stats.files === 1 ? '' : 's'} changed
              </span>
              {delivery.pr_url && (
                <a
                  href={delivery.pr_url}
                  target="_blank"
                  rel="noreferrer"
                  className="px-2.5 py-1 bg-inverse-primary text-white rounded-full text-xs font-bold uppercase tracking-wider flex items-center gap-1"
                >
                  <span className="material-symbols-outlined text-[14px]">open_in_new</span>
                  Open on GitHub
                </a>
              )}
            </div>
          </header>

          {delivery.error && (
            <div className="mb-6">
              <ErrorPanel title="Delivery error" detail={delivery.error} />
            </div>
          )}

          <div className="grid md:grid-cols-2 gap-4 mb-8">
            <div className="bg-surface-container border border-outline-variant rounded-lg p-6">
              <h2 className="text-xl font-semibold text-on-surface mb-4 border-b border-outline-variant pb-2">
                Git
              </h2>
              <dl className="space-y-2 text-sm">
                {[
                  ['Branch', delivery.branch_name],
                  ['Commit', shortSha(delivery.commit_sha)],
                  ['Base commit', shortSha(workflow.base_commit)],
                  ['Base branch', delivery.base_branch ?? '—'],
                  ['Pushed', delivery.pushed ? 'yes' : 'no'],
                  ['Target repo', delivery.target_repo ?? '— (GitHub not configured)'],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4">
                    <dt className="text-on-surface-variant">{label}</dt>
                    <dd className="font-mono text-[12px] text-on-surface text-right break-all">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="bg-surface-container border border-outline-variant rounded-lg p-6">
              <h2 className="text-xl font-semibold text-on-surface mb-4 border-b border-outline-variant pb-2">
                Pull request
              </h2>
              <dl className="space-y-2 text-sm">
                {[
                  ['State', delivery.pr_state ?? '—'],
                  ['Number', delivery.pr_number ? `#${delivery.pr_number}` : '—'],
                  ['Draft', delivery.pr_url ? (delivery.is_draft ? 'yes' : 'no') : '—'],
                  [
                    'Final verification',
                    delivery.verification_passed === null
                      ? '—'
                      : delivery.verification_passed
                        ? 'passed'
                        : 'failed',
                  ],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4">
                    <dt className="text-on-surface-variant">{label}</dt>
                    <dd className="font-mono text-[12px] text-on-surface text-right">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>

          {delivery.commit_message && (
            <div className="bg-surface-container border border-outline-variant rounded-lg p-6">
              <h2 className="text-xl font-semibold text-on-surface mb-4 border-b border-outline-variant pb-2">
                Commit message
              </h2>
              <pre className="font-mono text-[12px] text-on-surface-variant whitespace-pre-wrap">
                {delivery.commit_message}
              </pre>
            </div>
          )}
        </>
      )}
    </main>
  )
}
