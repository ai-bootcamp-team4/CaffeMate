import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import App from '../App'
import type { AuthGateway, AuthSession } from '../auth'
import type { AreaSearchResult, ControlApiClient, FeedbackPreview, HeadFence, PreparationGuide, Project, ResultExplanation, ResultView, WorkflowProgress } from '../apiClient'


export const head: HeadFence = {
  workflow_generation: 1,
  state_version: 1,
  founder_snapshot_id: 'founder-1',
  area_snapshot_id: 'area-1',
  evidence_snapshot_id: 'evidence-1',
  policy_snapshot_id: 'policy-v1',
  index_generation_id: 'index-1',
  seed_registry_id: 'seed-1',
}

export const project: Project = {
  project_id: 'project-1',
  user_id: 'user-1',
  created_at: '2026-08-22T00:00:00Z',
  state: {
    state_version: 1,
    status: 'ANALYZING',
    founder: { own_funds_krw: 80_000_000, borrowing_intent: 'UNDECIDED' },
    area: {
      resolution_status: 'RESOLVED',
      display_name: '수원시 영통구 원천동',
      coverage_profile: 'NO_NATIONWIDE_FACTS',
      evidence_ids: ['evidence-area'],
      unavailable_fields: ['administrative_dong_mapping', 'estimated_store_sales'],
    },
    updated_at: '2026-08-22T00:01:00Z',
  },
}

export const result: ResultView = {
  result_bundle_id: 'result-1',
  project_id: 'project-1',
  workflow_run_id: 'workflow-1',
  head,
  current_head: head,
  primary_candidate_id: 'candidate-1',
  audit_status: 'PASSED',
  created_at: '2026-08-22T00:02:00Z',
  freshness: 'CURRENT',
  stale_head_dimensions: [],
  invalidation_reason_codes: [],
  candidates: [{
    candidate_id: 'candidate-1', project_id: 'project-1', state_version: 1,
    case_type: 'FRANCHISE', display_name: '실제 검증 브랜드', review_status: 'REVIEW_RECOMMENDED',
    reason_codes: ['CURRENT_CONSTRAINTS_SATISFIED'], summary: '현재 계산 조건은 통과했고, 특정 주소 출점 승인은 본사 확인이 별도로 남아 있습니다.',
    rank: 1, rank_basis: 'NEXT_REVIEW_PRIORITY', is_primary_next_review: true,
    franchise: { brand_id: 'brand-1', eligibility: 'VERIFIED', availability_status: 'HQ_CONFIRMATION_REQUIRED', eligibility_evidence_refs: ['evidence-franchise'], disclosure_evidence_refs: [] },
    independent_model: null, evidence_refs: ['evidence-franchise'], assumption_refs: ['assumption-rent'],
    market_signals: [
      { signal_type: 'CAFE_COUNT', value: 208, unit: 'STORES', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do', evidence_id: 'evidence-market-cafes', caveat: '선택 지역에 연결된 행정동의 카페 업종 집계이며 개별 점포의 경쟁력을 뜻하지 않습니다.', decision_role: 'CONTEXT_ONLY' },
      { signal_type: 'ESTIMATED_SALES', value: 2_596_733_728, unit: 'KRW_PER_QUARTER_ESTIMATE', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-15572/A/1/datasetView.do', evidence_id: 'evidence-market-sales', caveat: '선택 지역의 카페 업종 분기 추정매출 합계이며 신규 점포 예상매출이 아닙니다.', decision_role: 'CONTEXT_ONLY' },
      { signal_type: 'FOOT_TRAFFIC', value: 12_465_323, unit: 'PERSON_VISITS_PER_QUARTER_ESTIMATE', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do', evidence_id: 'evidence-market-foot', caveat: '선택 지역의 분기 추정 유동인구이며 고유 방문자 수가 아닙니다.', decision_role: 'CONTEXT_ONLY' },
      { signal_type: 'RESIDENT_POPULATION', value: 37_068, unit: 'PERSONS', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-22182/S/1/datasetView.do', evidence_id: 'evidence-market-resident', caveat: '선택 지역에 연결된 행정동의 거주인구 합계입니다.', decision_role: 'CONTEXT_ONLY' },
      { signal_type: 'WORKER_POPULATION', value: 7_365, unit: 'PERSONS', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-22184/A/1/datasetView.do', evidence_id: 'evidence-market-worker', caveat: '선택 지역에 연결된 행정동의 직장인구 합계입니다.', decision_role: 'CONTEXT_ONLY' },
    ],
    official_documents: [{
      title: '커피전문점 영업신고 및 사업자등록',
      source_ref: 'https://easylaw.go.kr/coffee-registration',
      data_date: '2026-07-15',
      freshness_status: 'FRESH',
      document_version: 'easylaw-csmSeq-706@2026-07-15',
      excerpt: '휴게음식점 영업 신고 후 사업자등록을 진행합니다.',
      purposes: ['창업 절차 확인'],
      evidence_refs: ['evidence-official-procedure'],
      used_in_candidate: false,
    }],
    official_document_gaps: ['계약 전 확인 공식 문서', '정보공개서 공식 문서'],
    financial_summary: {
      initial_cash: { currency: 'KRW', low: 70_000_000, base: 80_000_000, high: 90_000_000, provenance_refs: ['evidence-cost'] },
      monthly_fixed_cost: { currency: 'KRW', low: 4_000_000, base: 5_000_000, high: 6_000_000, provenance_refs: ['evidence-cost'] },
      break_even_monthly_sales_krw: 15_000_000, required_daily_orders: 80, unknown_cost_fields: [],
    },
    missing_fields: [],
    risks: [
      { risk_id: 'risk-1', severity: 'HIGH', summary: '출점 가능 여부가 확인되지 않았습니다.', evidence_refs: [] },
      { risk_id: 'risk-2', severity: 'HIGH', summary: '출점 가능 여부가 확인되지 않았습니다.', evidence_refs: [] },
    ],
    counterfactuals: [{ variable: 'rent', condition: '월세 15% 감소', decision_impact: '검토 우선순위가 상승합니다.' }],
    next_actions: ['본사 출점 가능 여부 확인'],
    decision_inputs: [
      { field: 'own_funds_krw', label: '현재 자기자금', value: 80_000_000, provenance: 'USER_INPUT', resolution_status: 'USER_CONFIRMED_FACT', decision_role: 'CONSTRAINT_INPUT', source: null, applied_to: ['CAPITAL'], replaceable_by: [], limitation_code: null, resolution_action: { type: 'USER_INPUT', target_fields: ['own_funds_krw'] } },
      { field: 'initial_cash_krw', label: '초기 필요자금', range: { currency: 'KRW', low: 70_000_000, base: 80_000_000, high: 90_000_000, provenance_refs: ['evidence-cost'] }, provenance: 'BENCHMARK', resolution_status: 'RESOLVED_BENCHMARK', decision_role: 'FINANCE_INPUT', source: { title: '한국부동산원 상업용부동산 임대동향조사', source_ref: 'https://www.reb.or.kr', data_date: '2026-06-30', geographic_scope: '수원시 영통구' }, applied_to: ['INITIAL_CASH'], replaceable_by: ['PROPERTY_TERMS'], limitation_code: 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY', resolution_action: { type: 'PROPERTY_TERMS', target_fields: ['deposit_krw', 'monthly_rent_krw', 'management_fee_krw'] } },
      { field: 'equipment_cost_krw', label: '장비비 참고값', range: { currency: 'KRW', low: 12_000_000, base: 15_000_000, high: 18_000_000, provenance_refs: ['assumption-equipment'] }, provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', decision_role: 'FINANCE_INPUT', source: null, applied_to: ['INITIAL_CASH'], replaceable_by: ['EQUIPMENT_QUOTE'], limitation_code: null, resolution_action: { type: 'DOCUMENT_INTAKE', target_fields: ['equipment_cost_krw'], accepted_document_types: ['EQUIPMENT_QUOTE'] } },
    ],
    decision_trace: { gates: [{ gate_type: 'CAPITAL', status: 'PASS', reason_code: 'CURRENT_CONSTRAINTS_SATISFIED', decisive_input_refs: ['own_funds_krw', 'initial_cash_krw'], metrics: { own_funds_krw: 80_000_000, minimum_required_krw: 70_000_000, remaining_at_minimum_krw: 10_000_000 } }] },
    rank_trace: { basis: 'NEXT_REVIEW_PRIORITY', factors: [{ code: 'INITIAL_CASH_BASE', value: 80_000_000 }], decisive_factor: 'INITIAL_CASH_BASE' },
    verification_requirements: [{ requirement_code: 'HQ_AREA_APPROVAL', label: '이 주소의 출점 가능 여부', resolver: 'FRANCHISE_HQ', authority: '브랜드 본사', current_status: 'EXTERNAL_CONFIRMATION_REQUIRED', required_evidence: ['본사 서면 확인'], reason: '특정 주소의 출점 승인 여부는 CaffeMate가 확정할 수 없습니다.', resolution_action: { type: 'EXTERNAL_CONFIRMATION', target_fields: ['franchise_area_approval'] } }],
  }],
}

export const feedbackPreview: FeedbackPreview = {
  preview_id: 'feedback-preview-1',
  project_id: 'project-1',
  result_bundle_id: 'result-1',
  head,
  status: 'REVIEW_REQUIRED',
  latest_user_input: '저가 브랜드는 제외하고 10평 이하로 다시 보고 싶어요.',
  before_founder: { preferred_store_area_pyeong: 20 },
  after_founder: { preferred_store_area_pyeong: 10 },
  operations: [],
  clarifying_questions: [],
  affected_stage_codes: [],
  risk_flags: [],
  proposal_digest: `sha256:${'d'.repeat(64)}`,
}

export const resultExplanation: ResultExplanation = {
  explanation_id: 'explanation-1',
  result_bundle_id: 'result-1',
  candidate_id: 'candidate-1',
  intent: 'WHY_RECOMMENDED',
  conclusion: '현재 자금에 가장 가까운 후보지만 본사 확인이 필요합니다.',
  reasons: ['초기 필요자금 하한이 비교 후보 중 가장 낮습니다.'],
  evidence: [{
    evidence_id: 'evidence-market-cafes',
    label: '카페 점포 수',
    value: '208개',
    source_title: '서울시 상권분석서비스',
    source_ref: 'https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do',
    data_date: '2026-03-31',
    caveat: '행정동 집계이며 개별 점포 경쟁력을 뜻하지 않습니다.',
  }],
  unknowns: ['본사의 실제 출점 가능 여부'],
  decision_change_conditions: ['본사가 출점을 거절하면 이 후보는 제외됩니다.'],
  suggested_action: 'NONE',
  state_changed: false,
}

export const workflow = { workflow_run_id: 'workflow-1', project_id: 'project-1', workflow_code: 'FIRST_PROPOSAL' as const, status: 'SUCCEEDED' as const, head, created_at: '2026-08-22T00:01:00Z', updated_at: '2026-08-22T00:02:00Z' }
export const progress: WorkflowProgress = { ...workflow, stages: [], completed_stage_count: 6, total_stage_count: 6, current_stage_codes: [], terminal_reason_codes: [], human_review_requests: [], poll_after_ms: null }
export const preparationGuide: PreparationGuide = {
  project_id: 'project-1',
  selection_id: 'selection-1',
  candidate_id: 'candidate-1',
  candidate_type: 'FRANCHISE',
  jurisdiction_code: '4111756000',
  jurisdiction_display_name: '경기도 수원시 영통구 원천동',
  as_of: '2026-08-23',
  status: 'REVIEW_REQUIRED',
  procedures: [{
    procedure_type: 'HYGIENE_EDUCATION',
    status: 'OK',
    steps: [{ procedure_type: 'HYGIENE_EDUCATION', step_order: 1, title: '신규 영업자 위생교육 이수', required: true, authority: '관할 위생교육기관', source_date: '2026-08-23', evidence_id: 'evidence-procedure-1' }],
    missing_fields: [], conflicts: [], error_codes: [],
  }],
  human_actions_only: true,
  external_submission_performed: false,
  generated_at: '2026-08-23T00:00:00Z',
}

export function setup(nextResult: ResultView = result) {
  const session: AuthSession = { uid: 'user-1', displayName: '민석', getIdToken: vi.fn(async () => 'id-token'), signOut: vi.fn(async () => undefined) }
  const authGateway: AuthGateway = { restoreSession: vi.fn(async () => null), signIn: vi.fn(async () => session) }
  const client: ControlApiClient = {
    createProject: vi.fn(async () => ({ ...project, state: null })),
    listProjects: vi.fn(async () => []),
    searchAreas: vi.fn(async (_projectId, query): Promise<AreaSearchResult> => ({
      query,
      status: 'OK',
      completeness: 'UNVERIFIED',
      missing_fields: [],
      source_trace: [],
      candidates: [{
        area_id: 'legal-dong:4111710300',
        scope_type: 'LEGAL_DONG',
        display_name: '경기도 수원시 영통구 원천동',
        legal_dong_code: '4111710300',
        administrative_dong_codes: [],
        mapping_status: 'UNVERIFIED',
        source_revision: 'JUSO_LIVE_UNVERSIONED',
        boundary_version: null,
        selection_token: 'signed-area-selection',
      }],
    })),
    confirmOnboarding: vi.fn(async () => project),
    startFirstProposal: vi.fn(async () => workflow),
    getWorkflow: vi.fn(async () => progress),
    getResult: vi.fn(async () => nextResult),
    explainResult: vi.fn(async () => resultExplanation),
    createFeedbackPreview: vi.fn(async () => { throw new Error('not used') }),
    confirmFeedback: vi.fn(async () => { throw new Error('not used') }),
    cancelFeedback: vi.fn(async () => { throw new Error('not used') }),
    selectCandidate: vi.fn(async () => ({ selection_id: 'selection-1', candidate_id: 'candidate-1', selected_state_version: 2, required_evidence: [{ code: 'LEASE', title: '점포 임대 조건', status: 'REQUIRED', reason: '보증금·월세·권리금을 실제 값으로 확인합니다.' }], property_intake_enabled: true, document_intake_enabled: true })),
    getPreparationGuide: vi.fn(async () => preparationGuide),
    applyPropertyTerms: vi.fn(async (_projectId, _selectionId, _expectedStateVersion, terms) => ({ property_input_id: 'property-1', project_id: 'project-1', selection_id: 'selection-1', candidate_id: 'candidate-1', applied_state_version: 3, terms, previous_financial_summary: result.candidates[0].financial_summary, recompute_workflow: workflow, input_kind: 'USER_CONFIRMED_PROPERTY_TERMS' as const, is_demo_fixture: false, created_at: '2026-08-23T00:01:00Z' })),
    beginDocumentUpload: vi.fn(async () => { throw new Error('not used') }),
    uploadDocument: vi.fn(async () => { throw new Error('not used') }),
    completeDocumentUpload: vi.fn(async () => { throw new Error('not used') }),
    getDocumentRevision: vi.fn(async () => { throw new Error('not used') }),
    getDocumentExtractionForm: vi.fn(async () => { throw new Error('not used') }),
    updateDocumentExtractionForm: vi.fn(async () => { throw new Error('not used') }),
    applyDocumentExtractionForm: vi.fn(async () => { throw new Error('not used') }),
  }
  render(<App authGateway={authGateway} apiFactory={() => client} />)
  return { authGateway, client }
}

export async function enterOnboarding() {
  fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
  await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })
}

export async function completeOnboarding() {
  await enterOnboarding()
  fireEvent.change(screen.getByLabelText('희망 지역'), { target: { value: '수원 원천동' } })
  fireEvent.click(await screen.findByRole('option', { name: /경기도 수원시 영통구 원천동/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.change(screen.getByLabelText('현재 자기자금'), { target: { value: '8000' } })
  fireEvent.click(screen.getByRole('radio', { name: /아직 미정/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /둘 다 비교/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /직접 전업 운영/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('button', { name: '분석 시작' }))
  await screen.findByRole('heading', { name: '실제 검증 브랜드' })
}
