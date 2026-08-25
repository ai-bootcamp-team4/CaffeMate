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

export interface DecisionMoneyRange {
  low: number | null
  base: number | null
  high: number | null
}

export type DecisionResolutionStatus =
  | 'RESOLVED_FACT'
  | 'RESOLVED_USER_CONFIRMED'
  | 'RESOLVED_BENCHMARK'
  | 'RESOLVED_DERIVED'
  | 'ASSUMED'
  | 'INPUT_REQUIRED'
  | 'DOCUMENT_REQUIRED'
  | 'EXTERNAL_CONFIRMATION_REQUIRED'
  | 'UNSUPPORTED_BY_DATA'

export type DecisionRole = 'FINANCE_INPUT' | 'CONSTRAINT_INPUT' | 'VERIFICATION_ONLY' | 'CONTEXT_ONLY'
export type ResolutionActionType = 'PROPERTY_TERMS' | 'DOCUMENT_INTAKE' | 'USER_INPUT' | 'EXTERNAL_CONFIRMATION' | 'NONE'

export interface ResolutionAction {
  action_type: ResolutionActionType
  target_fields: string[]
  accepted_document_types: string[]
}

export interface DecisionDerivation {
  formula_code: string
  inputs: Record<string, unknown>
  coverage_status?: string | null
  floor_basis?: string | null
  source_version?: string | null
  reporting_year?: number | null
  constituent_evidence_refs?: string[]
}

export interface DecisionInput {
  field: string
  value_range_krw: DecisionMoneyRange | null
  value_bps: number | null
  provenance: 'FACT' | 'USER_INPUT' | 'BENCHMARK' | 'ASSUMPTION' | 'DERIVED' | 'UNKNOWN'
  resolution_status: DecisionResolutionStatus
  decision_role: DecisionRole
  source_title: string | null
  source_ref: string | null
  data_date: string | null
  geographic_scope: Record<string, unknown> | null
  source_anchor: string | null
  applied_to: string[]
  replaceable_by: ResolutionActionType[]
  resolution_action: ResolutionAction
  limitation_code: string | null
  derivation: DecisionDerivation | null
}

export interface DecisionGateTrace {
  gate_type: 'CAPITAL'
  status: 'PASS' | 'CONDITIONAL' | 'FAIL'
  reason_code: string
  decisive_input_refs: string[]
  metrics: {
    own_funds_krw: number
    minimum_required_krw: number | null
    maximum_required_krw: number | null
    shortfall_krw: number | null
  }
}

export interface RankTrace {
  ranking_class: string
  factors: Array<{
    factor_code: string
    value: number | string | null
    direction: 'ASC' | 'DESC'
  }>
  decisive_factor_code: string | null
  compared_candidate_id: string | null
  tie_break_used: boolean
}

export interface VerificationRequirement {
  requirement_id: string
  status: 'EXTERNAL_CONFIRMATION_REQUIRED'
  decision_role: 'VERIFICATION_ONLY'
  resolver: string
  reason_code: string
  required_evidence: string[]
  resolution_action: ResolutionAction
  why_caffemate_cannot_resolve: string
}

export interface PropertyContext {
  property_input_id: string
  address: string
  area_sqm: number
  floor: string | null
  deposit_krw: number
  monthly_rent_krw: number
  management_fee_krw: number
  key_money_krw: number | null
  provenance: 'USER_INPUT'
}

export interface ResultCandidate {
  schema_version: '2.0.0'
  candidate_id: string
  project_id: string
  state_version: number
  case_type: 'INDEPENDENT' | 'FRANCHISE'
  display_name: string
  review_status: 'REVIEW_RECOMMENDED' | 'CONDITIONAL_REVIEW' | 'EXCLUDED'
  reason_codes: string[]
  summary: string
  rank: number | null
  rank_basis: 'ECONOMIC_AND_FOUNDER_FIT' | 'NEXT_REVIEW_PRIORITY' | 'NOT_RANKED'
  is_primary_next_review: boolean
  franchise: {
    brand_id: string | null
    eligibility: 'VERIFIED' | 'UNVERIFIED' | 'INELIGIBLE'
    availability_status: 'AVAILABLE' | 'HQ_CONFIRMATION_REQUIRED' | 'UNAVAILABLE' | 'UNKNOWN'
    eligibility_evidence_refs: string[]
    disclosure_evidence_refs: string[]
    finance_profile?: Record<string, unknown>
  } | null
  independent_model: { model_id: string; adjusted_fields: string[] } | null
  property_context?: PropertyContext | null
  evidence_refs: string[]
  assumption_refs?: string[]
  market_signals?: Array<{
    signal_type: 'CAFE_COUNT' | 'OPEN_COUNT' | 'CLOSE_COUNT' | 'CLOSURE_RATE' | 'ESTIMATED_SALES' | 'FOOT_TRAFFIC' | 'RESIDENT_POPULATION' | 'WORKER_POPULATION'
    decision_role: DecisionRole
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
  gate_results?: DecisionGateTrace[]
  rank_trace?: RankTrace | null
  decision_inputs?: DecisionInput[]
  verification_requirements?: VerificationRequirement[]
  agent_advisory?: Record<string, unknown>
  financial_summary: {
    initial_cash: MoneyRange
    monthly_fixed_cost: MoneyRange
    base_contribution_margin_bps: number | null
    variable_cost_rate_bps: number | null
    effective_contribution_margin_bps: number | null
    break_even_monthly_sales_krw?: number | null
    required_daily_orders?: number | null
    unknown_cost_fields: string[]
  }
  missing_fields: Array<{ field: string; impact: string; next_check: string }>
  risks: Array<{ risk_id: string; severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'; summary: string; evidence_refs: string[] }>
  counterfactuals: Array<{ variable: string; condition: string; decision_impact: string }>
  next_actions: string[]
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
