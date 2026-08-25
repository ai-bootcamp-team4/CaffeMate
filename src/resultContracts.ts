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

export type DocumentType = 'COMMERCIAL_LEASE' | 'FRANCHISE_DISCLOSURE' | 'FRANCHISE_AGREEMENT' | 'INTERIOR_QUOTE' | 'EQUIPMENT_QUOTE' | 'PROPERTY_LISTING' | 'LOAN_TERMS' | 'BUSINESS_PROCEDURE' | 'OTHER'

export interface MoneyRange {
  currency: 'KRW'
  low: number | null
  base: number | null
  high: number | null
  provenance_refs: string[]
}

export type DecisionResolutionStatus =
  | 'RESOLVED_FACT'
  | 'USER_CONFIRMED_FACT'
  | 'RESOLVED_BENCHMARK'
  | 'DECLARED_ASSUMPTION'
  | 'INPUT_REQUIRED'
  | 'DOCUMENT_REQUIRED'
  | 'EXTERNAL_CONFIRMATION_REQUIRED'
  | 'UNSUPPORTED_BY_DATA'

export type DecisionRole = 'FINANCE_INPUT' | 'CONSTRAINT_INPUT' | 'VERIFICATION_ONLY' | 'CONTEXT_ONLY'

export interface DecisionSource {
  title: string
  source_ref: string | null
  data_date: string | null
  geographic_scope: string | null
  document_revision_id?: string | null
  filename?: string | null
  page_index?: number | null
  section_path?: string | null
}

export interface ResolutionAction {
  type: 'PROPERTY_TERMS' | 'DOCUMENT_INTAKE' | 'USER_INPUT' | 'EXTERNAL_CONFIRMATION' | 'NONE'
  target_fields: string[]
  accepted_document_types?: DocumentType[]
}

export interface DecisionInput {
  field: string
  label?: string | null
  value?: string | number | boolean | null
  range?: MoneyRange | null
  provenance: 'FACT' | 'USER_INPUT' | 'BENCHMARK' | 'ASSUMPTION' | 'DERIVED' | 'UNKNOWN'
  resolution_status: DecisionResolutionStatus
  decision_role: DecisionRole
  source: DecisionSource | null
  applied_to: string[]
  replaceable_by: string[]
  limitation_code: string | null
  resolution_action: ResolutionAction | null
}

export interface DecisionGateTrace {
  gate_type: string
  status: 'PASS' | 'CONDITIONAL' | 'FAIL'
  reason_code: string
  decisive_input_refs: string[]
  metrics: Record<string, string | number | boolean | null>
}

export interface RankTrace {
  basis: string
  factors: Array<{ code: string; value: string | number | boolean | null }>
  decisive_factor: string | null
}

export interface VerificationRequirement {
  requirement_code: string
  label: string
  resolver: string
  authority: string | null
  current_status: string
  required_evidence: string[]
  reason: string
  resolution_action: ResolutionAction | null
}

export interface CandidateInputChange {
  field: string
  before: DecisionInput | null
  after: DecisionInput | null
  applied_to: string[]
}

export interface CandidateDecisionChange {
  candidate_key: string
  display_name: string | null
  change_type: string
  previous_rank: number | null
  current_rank: number | null
  previous_review_status: ResultCandidate['review_status'] | null
  current_review_status: ResultCandidate['review_status'] | null
  initial_cash_base_delta_krw: number | null
  monthly_fixed_cost_base_delta_krw: number | null
  break_even_monthly_sales_delta_krw: number | null
  reason_codes_added?: string[]
  reason_codes_removed?: string[]
  input_changes?: CandidateInputChange[]
  gate_changes?: Array<{ gate_type: string; previous_status: string | null; current_status: string | null; reason_code: string | null }>
}

export interface ResultDecisionDelta {
  previous_result_bundle_id: string
  current_result_bundle_id: string
  primary_candidate_changed: boolean
  candidate_changes: CandidateDecisionChange[]
  requires_human_review: boolean
  human_review_reason_codes: string[]
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
    decision_role: DecisionRole
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
  decision_inputs?: DecisionInput[]
  decision_trace?: { gates: DecisionGateTrace[] }
  rank_trace?: RankTrace | null
  verification_requirements?: VerificationRequirement[]
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
  decision_delta?: ResultDecisionDelta | null
}
