import type { AuthSession } from './auth'
import type { OnboardingValues } from './onboardingState'

export type WorkflowStatus = 'QUEUED' | 'RUNNING' | 'WAITING_FOR_HUMAN' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED' | 'CANCELLED' | 'STALE'

export interface HeadFence {
  workflow_generation: number
  state_version: number
  founder_snapshot_id: string | null
  area_snapshot_id: string | null
  evidence_snapshot_id: string | null
  policy_snapshot_id: string
  index_generation_id: string | null
  seed_registry_id: string | null
}

export interface Project {
  project_id: string
  user_id: string
  created_at: string
  state: {
    state_version: number
    status: string
    founder: Record<string, unknown>
    area: {
      resolution_status: string
      area_id?: string | null
      scope_type?: 'LEGAL_DONG' | 'ADMINISTRATIVE_DONG' | 'COMPOSITE' | null
      legal_dong_code?: string | null
      administrative_dong_codes?: string[]
      mapping_status?: 'VERIFIED' | 'UNVERIFIED' | null
      display_name: string | null
      coverage_profile: string
      evidence_ids: string[]
      unavailable_fields: string[]
    }
    updated_at: string
  } | null
}

export interface AreaSearchCandidate {
  area_id: string
  scope_type: 'LEGAL_DONG' | 'ADMINISTRATIVE_DONG' | 'COMPOSITE'
  display_name: string
  legal_dong_code: string | null
  administrative_dong_codes: string[]
  mapping_status: 'VERIFIED' | 'UNVERIFIED'
  source_revision: string
  boundary_version: string | null
  selection_token: string
}

export interface AreaSearchResult {
  query: string
  status: string
  completeness: 'COMPLETE' | 'TRUNCATED' | 'UNVERIFIED'
  candidates: AreaSearchCandidate[]
  missing_fields: string[]
  source_trace: Array<Record<string, unknown>>
}

export interface WorkflowRun {
  workflow_run_id: string
  project_id: string
  workflow_code: 'FIRST_PROPOSAL'
  status: WorkflowStatus
  head: HeadFence
  created_at: string
  updated_at: string
}

export interface WorkflowProgress extends WorkflowRun {
  completed_stage_count: number
  total_stage_count: number
  current_stage_codes: string[]
  terminal_reason_codes: string[]
  human_review_requests: Array<{ stage_code: string; reason_codes: string[] }>
  poll_after_ms: number | null
}

export interface MoneyRange {
  currency: 'KRW'
  low: number | null
  base: number | null
  high: number | null
  provenance_refs: string[]
}

export interface ResultCandidate {
  candidate_id: string
  project_id: string
  state_version: number
  case_type: 'INDEPENDENT' | 'FRANCHISE'
  display_name: string
  review_status: 'REVIEW_RECOMMENDED' | 'CONDITIONAL_REVIEW' | 'EXCLUDED'
  reason_codes: string[]
  summary: string
  rank: number | null
  rank_basis: string
  is_primary_next_review: boolean
  franchise: {
    brand_id: string | null
    eligibility: 'VERIFIED' | 'UNVERIFIED' | 'INELIGIBLE'
    availability_status: 'AVAILABLE' | 'HQ_CONFIRMATION_REQUIRED' | 'UNAVAILABLE' | 'UNKNOWN'
    eligibility_evidence_refs: string[]
    disclosure_evidence_refs: string[]
  } | null
  independent_model: { model_id: string; adjusted_fields: string[] } | null
  evidence_refs: string[]
  assumption_refs?: string[]
  market_signals?: Array<{
    signal_type: 'CAFE_COUNT' | 'OPEN_COUNT' | 'CLOSE_COUNT' | 'CLOSURE_RATE' | 'ESTIMATED_SALES'
    value: number
    unit: string | null
    data_date: string | null
    freshness_status: 'FRESH' | 'STALE' | 'UNKNOWN' | 'NOT_APPLICABLE'
    source_title: string
    source_ref: string
    evidence_id: string
    caveat: string
  }>
  financial_summary: {
    initial_cash: MoneyRange
    monthly_fixed_cost: MoneyRange
    break_even_monthly_sales_krw?: number | null
    required_daily_orders?: number | null
    unknown_cost_fields: string[]
  }
  missing_fields: Array<{ field: string; impact: string; next_check: string }>
  risks: Array<{ risk_id: string; severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'; summary: string; evidence_refs: string[] }>
  counterfactuals: Array<{ variable: string; condition: string; decision_impact: string }>
  next_actions: string[]
}

export interface ResultView {
  result_bundle_id: string
  project_id: string
  workflow_run_id: string
  head: HeadFence
  candidates: ResultCandidate[]
  primary_candidate_id: string
  audit_status: 'PASSED' | 'REQUIRES_HUMAN' | 'UNAVAILABLE'
  created_at: string
  freshness: 'CURRENT' | 'STALE'
  stale_head_dimensions: string[]
  current_head: HeadFence
  invalidation_reason_codes: string[]
}

export interface FeedbackPreview {
  preview_id: string
  project_id: string
  result_bundle_id: string
  head: HeadFence
  status: 'PROCESSING' | 'REVIEW_REQUIRED' | 'CLARIFICATION_REQUIRED' | 'NOOP' | 'UNSUPPORTED' | 'EXPIRED' | 'CONFIRMED' | 'CANCELLED'
  latest_user_input: string
  before_founder: Record<string, unknown>
  after_founder: Record<string, unknown> | null
  operations: Array<Record<string, unknown>>
  clarifying_questions: string[]
  affected_stage_codes: string[]
  risk_flags: string[]
  proposal_digest: string | null
}

export interface FeedbackResolution {
  preview: FeedbackPreview
  state_version: number | null
  workflow: WorkflowRun | null
}

export interface CandidateSelection {
  selection_id: string
  candidate_id: string
  required_evidence: Array<{ code: string; title: string; status: string; reason: string }>
  property_intake_enabled: boolean
  document_intake_enabled: boolean
}

export interface ControlApiClient {
  createProject(): Promise<Project>
  listProjects(): Promise<Project[]>
  searchAreas(projectId: string, query: string): Promise<AreaSearchResult>
  confirmOnboarding(projectId: string, values: OnboardingValues, areaSelectionToken: string): Promise<Project>
  startFirstProposal(projectId: string): Promise<WorkflowRun>
  getWorkflow(projectId: string, workflowRunId: string): Promise<WorkflowProgress>
  getResult(projectId: string): Promise<ResultView>
  createFeedbackPreview(projectId: string, input: string): Promise<FeedbackPreview>
  confirmFeedback(projectId: string, preview: FeedbackPreview): Promise<FeedbackResolution>
  cancelFeedback(projectId: string, previewId: string): Promise<FeedbackResolution>
  selectCandidate(projectId: string, result: ResultView, candidateId: string): Promise<CandidateSelection>
}

export class ControlApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message)
    this.name = 'ControlApiError'
  }
}

type FetchLike = typeof fetch

export function createControlApiClient(
  session: AuthSession,
  options: { baseUrl?: string; fetchImpl?: FetchLike; idempotencyKey?: () => string } = {},
): ControlApiClient {
  const baseUrl = (options.baseUrl ?? window.__CAFFEMATE_CONFIG__?.CONTROL_API_BASE_URL ?? import.meta.env.VITE_CONTROL_API_BASE_URL)?.replace(/\/$/, '')
  if (!baseUrl) throw new Error('CONTROL_API_CONFIG_MISSING:VITE_CONTROL_API_BASE_URL')
  const fetchImpl = options.fetchImpl ?? fetch
  const idempotencyKey = options.idempotencyKey ?? (() => crypto.randomUUID())

  async function request<T>(path: string, init: RequestInit = {}, idempotent = false): Promise<T> {
    const token = await session.getIdToken()
    const response = await fetchImpl(`${baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        ...(idempotent ? { 'Idempotency-Key': idempotencyKey() } : {}),
        ...init.headers,
      },
    })
    const body = await response.json().catch(() => null) as { code?: string; message?: string } | T | null
    if (!response.ok) {
      const error = body as { code?: string; message?: string } | null
      throw new ControlApiError(response.status, error?.code ?? 'CONTROL_API_ERROR', error?.message ?? `요청에 실패했습니다. (${response.status})`)
    }
    return body as T
  }

  return {
    createProject: () => request('/v1/projects', { method: 'POST', body: '{}' }, true),
    listProjects: () => request('/v1/projects'),
    searchAreas: (projectId, query) => request(`/v1/projects/${projectId}/areas:search`, {
      method: 'POST',
      body: JSON.stringify({ query, limit: 10 }),
    }),
    confirmOnboarding: (projectId, values, areaSelectionToken) => request(`/v1/projects/${projectId}/onboarding/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        area_selection_token: areaSelectionToken,
        founder: {
          target_area_input: values.targetAreaInput.trim(),
          own_funds_krw: Number(values.ownFundsKrw),
          borrowing_intent: values.borrowingIntent,
          cafe_type_preference: values.cafeTypePreference,
          operation_mode: values.operationMode,
          desired_opening_period: values.desiredOpeningPeriod.trim() || null,
          prior_cafe_experience: values.priorCafeExperience.trim() || null,
          preferences: [],
          avoidances: [],
        },
      }),
    }, true),
    startFirstProposal: (projectId) => request(`/v1/projects/${projectId}/workflows/FIRST_PROPOSAL`, { method: 'POST', body: '{}' }, true),
    getWorkflow: (projectId, workflowRunId) => request(`/v1/projects/${projectId}/workflows/${workflowRunId}`),
    getResult: (projectId) => request(`/v1/projects/${projectId}/result`),
    createFeedbackPreview: (projectId, input) => request(`/v1/projects/${projectId}/feedback/previews`, { method: 'POST', body: JSON.stringify({ input }) }, true),
    confirmFeedback: (projectId, preview) => request(`/v1/projects/${projectId}/feedback/${preview.preview_id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ expected_head: preview.head, proposal_digest: preview.proposal_digest }),
    }, true),
    cancelFeedback: (projectId, previewId) => request(`/v1/projects/${projectId}/feedback/${previewId}/cancel`, { method: 'POST', body: '{}' }, true),
    selectCandidate: (projectId, result, candidateId) => request(`/v1/projects/${projectId}/candidate-selections`, {
      method: 'POST',
      body: JSON.stringify({ result_bundle_id: result.result_bundle_id, candidate_id: candidateId, expected_head: result.current_head }),
    }, true),
  }
}

export const terminalWorkflowStatuses = new Set<WorkflowStatus>(['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED', 'STALE', 'WAITING_FOR_HUMAN'])

export async function waitForWorkflow(
  client: ControlApiClient,
  projectId: string,
  initial: WorkflowRun,
  onProgress?: (progress: WorkflowProgress) => void,
): Promise<WorkflowProgress> {
  let progress = await client.getWorkflow(projectId, initial.workflow_run_id)
  onProgress?.(progress)
  while (!terminalWorkflowStatuses.has(progress.status)) {
    await new Promise((resolve) => window.setTimeout(resolve, Math.max(250, Math.min(progress.poll_after_ms ?? 1000, 30_000))))
    progress = await client.getWorkflow(projectId, initial.workflow_run_id)
    onProgress?.(progress)
  }
  return progress
}
