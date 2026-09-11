import Link from 'next/link'
import { getWorkflow, type Workflow } from '@/lib/api'
import { ErrorPanel } from '@/components/error-panel'

export const dynamic = 'force-dynamic'

export default async function EvidencePage({ params }: { params: Promise<{ id: string }> }) {
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

  return (
    <main className="p-6 max-w-7xl mx-auto">
      <div className="mb-6 text-sm text-on-surface-variant flex items-center gap-2">
        <Link href="/" className="hover:text-primary transition-colors">Issues</Link>
        <span className="material-symbols-outlined text-[16px]">chevron_right</span>
        <Link href={`/issues/${id}`} className="hover:text-primary transition-colors">
          {workflow.repo}
        </Link>
        <span className="material-symbols-outlined text-[16px]">chevron_right</span>
        <span className="text-on-surface">Verification Evidence</span>
      </div>

      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-on-surface mb-2">
          Verification Evidence
        </h1>
        <p className="text-sm text-on-surface-variant">
          Every check the sandbox ran against each candidate, with the command and its output.
        </p>
      </header>

      <div className="flex flex-col gap-6">
        {workflow.attempts.map((attempt) => (
          <section key={attempt.attempt_number} className="space-y-3">
            <h2 className="text-sm font-bold tracking-[0.05em] uppercase text-on-surface-variant">
              Attempt {attempt.attempt_number} — {attempt.status}
            </h2>

            {attempt.verification_results.map((step, index) => (
              <div
                key={`${step.check_name}-${index}`}
                className="bg-surface-container border border-outline-variant rounded overflow-hidden"
              >
                <div className="px-4 py-3 border-b border-outline-variant flex items-center justify-between bg-surface-container-low">
                  <div className="flex items-center gap-3">
                    <span className="material-symbols-outlined text-on-surface-variant">
                      {step.status === 'passed' ? 'check_circle' : step.status === 'failed' ? 'cancel' : 'science'}
                    </span>
                    <div>
                      <h3 className="font-semibold text-on-surface">{step.check_name}</h3>
                      <p className="font-mono text-[11px] text-on-surface-variant break-all">
                        {step.command}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-[11px] text-on-surface-variant font-mono">
                      exit {step.exit_code} · {step.duration_ms} ms
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-bold tracking-[0.05em] uppercase ${
                        step.status === 'passed'
                          ? 'bg-[rgba(74,222,128,0.1)] text-success border border-success/20'
                          : step.status === 'failed'
                            ? 'bg-[rgba(255,180,171,0.1)] text-error border border-error/20'
                            : 'bg-surface-container-highest text-on-surface-variant'
                      }`}
                    >
                      {step.status}
                    </span>
                  </div>
                </div>

                {(step.stdout || step.stderr) && (
                  <pre className="p-4 font-mono text-[11px] text-on-surface-variant whitespace-pre-wrap break-words max-h-80 overflow-y-auto bg-surface-container-lowest">
                    {[step.stdout, step.stderr].filter(Boolean).join('\n')}
                  </pre>
                )}
              </div>
            ))}

            {!attempt.verification_results.length && (
              <p className="text-xs text-on-surface-variant">No checks recorded for this attempt.</p>
            )}
          </section>
        ))}

        {!workflow.attempts.length && (
          <div className="bg-surface-container border border-outline-variant rounded p-4 text-xs text-on-surface-variant">
            {workflow.error ?? 'The run did not reach fix generation.'}
          </div>
        )}
      </div>
    </main>
  )
}
