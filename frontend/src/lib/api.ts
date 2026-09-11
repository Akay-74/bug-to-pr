/**
 * Typed client for the Bug-to-PR backend (docs/phase5.md "Frontend Integration").
 *
 * Every shape here mirrors backend/api/schemas.py. Nothing in the dashboard
 * invents data any more: if a field is not in one of these responses, the UI
 * does not show it.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000'

export type ValidationStatus =
  | 'pending'
  | 'valid'
  | 'invalid'
  | 'environment_error'
  | 'test_error'

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed'

export type RunStage =
  | 'intake'
  | 'baseline'
  | 'localization'
  | 'generation'
  | 'verification'
  | 'retry'
  | 'pr_creation'
  | 'completed'

export type AttemptStatus = 'pending' | 'running' | 'passed' | 'failed'

export type FailureType =
  | 'none'
  | 'generation_error'
  | 'patch_application_error'
  | 'verification_failed'
  | 'environment_error'
  | 'protected_test_modified'
  | 'timeout'

export type VerificationStatus = 'passed' | 'failed' | 'error'

export type DeliveryStatus = 'pending' | 'committed' | 'pr_created' | 'failed'

export interface LocalizationResult {
  rank: number
  file: string
  symbol: string
  symbol_type: string
  start_line: number
  end_line: number
  semantic_score: number
  lexical_score: number
  relevance_score: number
  final_score: number
  matched_terms: string[]
}

export interface VerificationResult {
  check_name: string
  status: VerificationStatus
  command: string
  exit_code: number
  stdout: string
  stderr: string
  duration_ms: number
}

export interface WorkflowAttempt {
  attempt_number: number
  model: string | null
  hypothesis: string | null
  diff: string | null
  status: AttemptStatus
  failure_type: FailureType | null
  verification_results: VerificationResult[]
  started_at: string | null
  completed_at: string | null
}

export interface WorkflowDelivery {
  delivery_id: string
  branch_name: string
  commit_sha: string | null
  commit_message: string | null
  status: DeliveryStatus
  failure_type: string | null
  error: string | null
  pushed: boolean
  target_repo: string | null
  base_branch: string | null
  pr_url: string | null
  pr_number: number | null
  pr_state: string | null
  is_draft: boolean
  verification_passed: boolean | null
  committed_at: string | null
  pr_created_at: string | null
}

export interface Workflow {
  run_id: string
  issue_id: string
  issue_url: string
  repo: string
  base_commit: string
  title: string
  problem: string
  benchmark_validation: ValidationStatus
  status: RunStatus
  current_stage: RunStage
  attempts_taken: number
  error: string | null
  localization: LocalizationResult[]
  attempts: WorkflowAttempt[]
  delivery: WorkflowDelivery | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface WorkflowSummary {
  issue_id: string
  issue_url: string
  repo: string
  title: string
  benchmark_validation: ValidationStatus
  run_id: string | null
  status: RunStatus | null
  current_stage: RunStage | null
  attempts_taken: number | null
  error: string | null
  pr_url: string | null
  delivery_status: DeliveryStatus | null
  completed_at: string | null
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      // Run state changes as the workflow progresses, so a cached page would
      // show a stale stage.
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (cause) {
    // A refused connection is the common case in development, and "failed to
    // fetch" alone does not tell the user the backend is not running.
    throw new ApiError(
      `Could not reach the backend at ${API_BASE}. Is it running? (${String(cause)})`,
      0,
    )
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* the body was not JSON; the status line is what we have */
    }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

/** Every benchmark issue with the state of its latest run. */
export function listWorkflows(): Promise<{ issues: WorkflowSummary[] }> {
  return request('/api/workflow')
}

/** The latest run for one issue. Returns null when it has never been run. */
export async function getWorkflow(issueId: string): Promise<Workflow | null> {
  try {
    return await request<Workflow>(`/api/workflow/${encodeURIComponent(issueId)}`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export function getWorkflowRun(runId: string): Promise<Workflow> {
  return request(`/api/workflow/run/${runId}`)
}

/** Run the complete workflow for an issue. Long: the caller should expect minutes. */
export function startWorkflow(issueId: string, createPr?: boolean): Promise<Workflow> {
  const query = createPr === undefined ? '' : `?create_pr=${createPr}`
  return request(`/api/workflow/${encodeURIComponent(issueId)}${query}`, { method: 'POST' })
}

/** Commit the validated fix, and open a draft PR if GitHub access is configured. */
export function createPullRequest(runId: string, commitOnly = false): Promise<unknown> {
  return request(`/api/pr/${runId}${commitOnly ? '/commit' : ''}`, { method: 'POST' })
}

// --- presentation helpers --------------------------------------------------

/** How a run should read in the issue table. */
export function runStatusLabel(
  summary: Pick<WorkflowSummary, 'status' | 'current_stage'>,
): 'verified' | 'failed' | 'running' | 'pending' {
  if (summary.status === 'completed') return 'verified'
  if (summary.status === 'failed') return 'failed'
  if (summary.status === 'running') return 'running'
  return 'pending'
}

export function formatDuration(from: string | null, to: string | null): string {
  if (!from || !to) return '—'
  const ms = new Date(to).getTime() - new Date(from).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 10) : '—'
}
