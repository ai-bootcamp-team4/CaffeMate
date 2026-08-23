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
    signal_type: 'CAFE_COUNT' | 'OPEN_COUNT' | 'CLOSE_COUNT' | 'CLOSURE_RATE' | 'ESTIMATED_SALES' | 'FOOT_TRAFFIC' | 'RESIDENT_POPULATION' | 'WORKER_POPULATION'
    value: number
    unit: string | null
    data_date: string | null
    freshness_status: 'FRESH' | 'STALE' | 'UNKNOWN' | 'NOT_APPLICABLE'
    source_title: string
    source_ref: string
    evidence_id: string
    caveat: string
  }>
  official_documents?: Array<{
    title: string
    source_ref: string
    data_date: string | null
    freshness_status: 'FRESH' | 'STALE' | 'UNKNOWN' | 'NOT_APPLICABLE'
    document_version: string
    excerpt: string
    purposes: string[]
    evidence_refs: string[]
    used_in_candidate: boolean
  }>
  official_document_gaps?: string[]
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
  primary_candidate_id: string | null
  audit_status: 'PASSED' | 'REQUIRES_HUMAN' | 'UNAVAILABLE'
  outcome_status?: 'REVIEWABLE_CANDIDATES' | 'NO_REVIEWABLE_CANDIDATES'
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
  selected_state_version: number
  required_evidence: Array<{ code: string; title: string; status: string; reason: string }>
  property_intake_enabled: boolean
  document_intake_enabled: boolean
}

export interface PropertyTermsInput {
  address: string
  area_sqm: number
  floor: string | null
  deposit_krw: number
  monthly_rent_krw: number
  management_fee_krw: number
  key_money_krw: number | null
}

export interface PropertyTermsApplication {
  property_input_id: string
  project_id: string
  selection_id: string
  candidate_id: string
  applied_state_version: number
  terms: PropertyTermsInput
  previous_financial_summary: ResultCandidate['financial_summary']
  recompute_workflow: WorkflowRun
  input_kind: 'USER_CONFIRMED_PROPERTY_TERMS'
  is_demo_fixture: boolean
  created_at: string
}

export type ProcedureType = 'BUSINESS_REGISTRATION' | 'FOOD_SERVICE_REPORT' | 'FACILITY_REQUIREMENTS' | 'HYGIENE_EDUCATION' | 'SIGNAGE' | 'FIRE_SAFETY'

export interface PreparationProcedure {
  procedure_type: ProcedureType
  status: 'OK' | 'PARTIAL' | 'STALE' | 'NOT_FOUND' | 'ERROR'
  steps: Array<{
    procedure_type: ProcedureType
    step_order: number
    title: string
    required: boolean
    authority: string
    source_date: string
    evidence_id: string
  }>
  missing_fields: string[]
  conflicts: string[]
  error_codes: string[]
}

export interface PreparationGuide {
  project_id: string
  selection_id: string
  candidate_id: string
  candidate_type: string
  jurisdiction_code: string
  jurisdiction_display_name: string | null
  as_of: string
  status: 'COMPLETE' | 'REVIEW_REQUIRED' | 'UNAVAILABLE'
  procedures: PreparationProcedure[]
  human_actions_only: boolean
  external_submission_performed: boolean
  generated_at: string
}

export type DocumentType = 'COMMERCIAL_LEASE' | 'FRANCHISE_DISCLOSURE' | 'FRANCHISE_AGREEMENT' | 'INTERIOR_QUOTE' | 'EQUIPMENT_QUOTE' | 'PROPERTY_LISTING' | 'LOAN_TERMS' | 'BUSINESS_PROCEDURE' | 'OTHER'
export type DocumentRevisionStatus = 'UPLOAD_PENDING' | 'VALIDATING' | 'SCAN_PENDING' | 'READY_FOR_PARSING' | 'PARSING' | 'EXTRACTION_READY' | 'APPLIED' | 'EXTRACTION_FAILED' | 'QUARANTINED' | 'DELETED'

export interface SignedDocumentUpload {
  document_id: string
  document_revision_id: string
  revision_number: number
  object_path: string
  upload_url: string
  method: 'PUT'
  required_headers: Record<string, string>
  expires_at: string
  status: DocumentRevisionStatus
}

export interface DocumentRevision {
  document_id: string
  document_revision_id: string
  project_id: string
  revision_number: number
  document_type: DocumentType
  original_filename: string
  content_type: string
  size_bytes: number
  sha256: string
  status: DocumentRevisionStatus
  failure_codes: string[]
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface DocumentExtractionField {
  field_id: string
  claim_type: string
  label: string
  raw_value_text: string | null
  extracted_value: string | number | boolean | null
  current_value: string | number | boolean | null
  unit: string | null
  materiality: string
  extraction_status: 'AUTO_FILLED' | 'REVIEW_REQUIRED' | 'UNRESOLVED'
  edit_status: 'UNCHANGED' | 'EDITED' | 'CLEARED'
  anchor: { page_index: number; section_path: string | null } | null
  warnings: string[]
}

export interface DocumentExtractionForm {
  form_id: string
  project_id: string
  document_id: string
  document_revision_id: string
  expected_state_version: number
  form_status: string
  fields: DocumentExtractionField[]
  apply_label: string
  form_digest: string | null
  applied_state_version: number | null
}

export interface ExtractionFormApplication {
  application_id: string
  project_id: string
  document_revision_id: string
  applied_state_version: number
  recompute_workflow_run_id: string
  claims: Array<Record<string, unknown>>
  conflicts: Array<Record<string, unknown>>
  requires_human_review: boolean
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
  getPreparationGuide(projectId: string, selectionId: string): Promise<PreparationGuide>
  applyPropertyTerms(projectId: string, selectionId: string, expectedStateVersion: number, terms: PropertyTermsInput): Promise<PropertyTermsApplication>
  beginDocumentUpload(projectId: string, file: File, documentType: DocumentType, sha256: string): Promise<SignedDocumentUpload>
  uploadDocument(upload: SignedDocumentUpload, file: File): Promise<void>
  completeDocumentUpload(projectId: string, documentRevisionId: string): Promise<DocumentRevision>
  getDocumentRevision(projectId: string, documentRevisionId: string): Promise<DocumentRevision>
  getDocumentExtractionForm(projectId: string, documentRevisionId: string): Promise<DocumentExtractionForm>
  updateDocumentExtractionForm(projectId: string, form: DocumentExtractionForm, edits: Array<{ field_id: string; value: string | number | boolean | null }>): Promise<DocumentExtractionForm>
  applyDocumentExtractionForm(projectId: string, form: DocumentExtractionForm): Promise<ExtractionFormApplication>
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
    getPreparationGuide: (projectId, selectionId) => request(`/v1/projects/${projectId}/candidate-selections/${selectionId}/preparation-guide`),
    applyPropertyTerms: (projectId, selectionId, expectedStateVersion, terms) => request(`/v1/projects/${projectId}/candidate-selections/${selectionId}/property-terms`, {
      method: 'POST',
      body: JSON.stringify({ expected_state_version: expectedStateVersion, terms }),
    }, true),
    beginDocumentUpload: (projectId, file, documentType, sha256) => request(`/v1/projects/${projectId}/documents/uploads`, {
      method: 'POST',
      body: JSON.stringify({ document_type: documentType, filename: file.name, content_type: file.type, size_bytes: file.size, sha256 }),
    }, true),
    uploadDocument: async (upload, file) => {
      const response = await fetchImpl(upload.upload_url, { method: upload.method, body: file, headers: upload.required_headers })
      if (!response.ok) throw new ControlApiError(response.status, 'DOCUMENT_UPLOAD_FAILED', `파일 전송에 실패했습니다. (${response.status})`)
    },
    completeDocumentUpload: (projectId, documentRevisionId) => request(`/v1/projects/${projectId}/documents/uploads:complete`, {
      method: 'POST', body: JSON.stringify({ document_revision_id: documentRevisionId }),
    }),
    getDocumentRevision: (projectId, documentRevisionId) => request(`/v1/projects/${projectId}/documents/${documentRevisionId}`),
    getDocumentExtractionForm: (projectId, documentRevisionId) => request(`/v1/projects/${projectId}/documents/${documentRevisionId}/extraction-form`),
    updateDocumentExtractionForm: (projectId, form, edits) => request(`/v1/projects/${projectId}/documents/${form.document_revision_id}/extraction-form`, {
      method: 'PUT', body: JSON.stringify({ expected_state_version: form.expected_state_version, edits }),
    }),
    applyDocumentExtractionForm: (projectId, form) => request(`/v1/projects/${projectId}/documents/${form.document_revision_id}/extraction-form:apply`, {
      method: 'POST', body: JSON.stringify({ expected_state_version: form.expected_state_version, expected_form_digest: form.form_digest }),
    }, true),
  }
}

export async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
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
