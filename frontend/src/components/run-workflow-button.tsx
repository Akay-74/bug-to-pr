'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { startWorkflow } from '@/lib/api'

/**
 * Starts the complete workflow for one issue.
 *
 * The backend endpoint is synchronous and a real benchmark repository takes
 * minutes (clone, install, model, containers), so the button says so and
 * stays disabled for the duration rather than looking hung.
 */
export function RunWorkflowButton({
  issueId,
  label = 'Run Workflow',
  className = '',
}: {
  issueId: string
  label?: string
  className?: string
}) {
  const router = useRouter()
  const [state, setState] = useState<'idle' | 'running'>('idle')
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setState('running')
    setError(null)
    try {
      await startWorkflow(issueId)
      router.refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setState('idle')
    }
  }

  return (
    <div className="inline-flex flex-col items-end gap-1">
      <button
        onClick={run}
        disabled={state === 'running'}
        className={`text-xs font-sans bg-inverse-primary text-white px-3 py-1 rounded hover:bg-primary-container transition-colors disabled:opacity-60 disabled:cursor-wait inline-flex items-center gap-1.5 ${className}`}
      >
        <span
          className={`material-symbols-outlined text-[14px] ${state === 'running' ? 'animate-spin' : ''}`}
        >
          {state === 'running' ? 'progress_activity' : 'play_arrow'}
        </span>
        {state === 'running' ? 'Running…' : label}
      </button>
      {state === 'running' && (
        <span className="text-[10px] text-on-surface-variant">
          May take several minutes
        </span>
      )}
      {error && <span className="text-[10px] text-error max-w-xs text-right">{error}</span>}
    </div>
  )
}
