import type { AuthGateway, AuthSession } from './auth'
import type {
  AreaSearchResult,
  ControlApiClient,
  DocumentExtractionForm,
  DocumentRevision,
  DocumentType,
  ExtractionFormApplication,
  FeedbackPreview,
  FeedbackResolution,
  HeadFence,
  PreparationGuide,
  Project,
  ResultExplanation,
  ResultView,
  SignedDocumentUpload,
  WorkflowProgress,
  WorkflowRun,
} from './apiClient'

const now = '2026-08-25T02:00:00Z'

const head: HeadFence = {
  workflow_generation: 1,
  state_version: 1,
  founder_snapshot_id: 'ui-only-founder',
  area_snapshot_id: 'ui-only-area',
  evidence_snapshot_id: 'ui-only-evidence',
  policy_snapshot_id: 'policy-v1',
  index_generation_id: 'ui-only-index',
  seed_registry_id: 'ui-only-seeds',
}

const project: Project = {
  project_id: 'ui-only-project',
  user_id: 'ui-only-user',
  created_at: now,
  state: {
    state_version: 1,
    status: 'READY',
    founder: { own_funds_krw: 80_000_000, borrowing_intent: 'UNDECIDED' },
    area: {
      resolution_status: 'RESOLVED',
      area_id: 'legal-dong:4111710300',
      scope_type: 'LEGAL_DONG',
      legal_dong_code: '4111710300',
      administrative_dong_codes: [],
      mapping_status: 'UNVERIFIED',
      display_name: '경기도 수원시 영통구 원천동',
      coverage_profile: 'NO_NATIONWIDE_FACTS',
      evidence_ids: ['ui-only-area-evidence'],
      unavailable_fields: ['administrative_dong_mapping'],
    },
    updated_at: now,
  },
}

const workflow: WorkflowRun = {
  workflow_run_id: 'ui-only-workflow',
  project_id: project.project_id,
  workflow_code: 'FIRST_PROPOSAL',
  status: 'SUCCEEDED',
  head,
  created_at: now,
  updated_at: now,
}

const progress: WorkflowProgress = {
  ...workflow,
  stages: [],
  completed_stage_count: 6,
  total_stage_count: 6,
  current_stage_codes: [],
  terminal_reason_codes: [],
  human_review_requests: [],
  poll_after_ms: null,
}

const initialResult: ResultView = {
  result_bundle_id: 'ui-only-result',
  project_id: project.project_id,
  workflow_run_id: workflow.workflow_run_id,
  head,
  current_head: head,
  primary_candidate_id: 'ui-only-independent',
  audit_status: 'PASSED',
  outcome_status: 'REVIEWABLE_CANDIDATES',
  created_at: now,
  freshness: 'CURRENT',
  stale_head_dimensions: [],
  invalidation_reason_codes: [],
  candidates: [
    {
      candidate_id: 'ui-only-independent',
      project_id: project.project_id,
      state_version: 1,
      case_type: 'INDEPENDENT',
      display_name: '소형 포장 중심 개인카페',
      review_status: 'REVIEW_RECOMMENDED',
      reason_codes: ['CURRENT_CONSTRAINTS_SATISFIED'],
      summary: 'UI 확인용 예시 후보입니다. 실제 계산이나 추천 결과가 아닙니다.',
      rank: 1,
      rank_basis: 'UI_ONLY_FIXTURE',
      is_primary_next_review: true,
      franchise: null,
      independent_model: { model_id: 'compact-takeout', adjusted_fields: ['operations.owner_hours_per_week'] },
      evidence_refs: ['ui-only-evidence'],
      assumption_refs: ['ui-only-assumption'],
      market_signals: [
        { signal_type: 'CAFE_COUNT', value: 42, unit: 'STORES', data_date: '2026-06-30', freshness_status: 'FRESH', source_title: 'UI 전용 예시 데이터', source_ref: 'ui-only://market/cafes', evidence_id: 'ui-only-market-cafes', caveat: '화면 확인을 위한 로컬 fixture입니다.', decision_role: 'CONTEXT_ONLY' },
        { signal_type: 'FOOT_TRAFFIC', value: 185000, unit: 'PERSON_VISITS_PER_MONTH_ESTIMATE', data_date: '2026-06-30', freshness_status: 'FRESH', source_title: 'UI 전용 예시 데이터', source_ref: 'ui-only://market/traffic', evidence_id: 'ui-only-market-traffic', caveat: '화면 확인을 위한 로컬 fixture입니다.', decision_role: 'CONTEXT_ONLY' },
      ],
      official_documents: [],
      official_document_gaps: ['임대 조건 확인', '실제 견적 확인'],
      financial_summary: {
        initial_cash: { currency: 'KRW', low: 55_000_000, base: 65_000_000, high: 78_000_000, provenance_refs: ['ui-only-assumption'] },
        monthly_fixed_cost: { currency: 'KRW', low: 3_000_000, base: 3_800_000, high: 4_600_000, provenance_refs: ['ui-only-assumption'] },
        break_even_monthly_sales_krw: 12_500_000,
        required_daily_orders: 72,
        unknown_cost_fields: ['premium'],
      },
      missing_fields: [{ field: 'premium', impact: '초기 필요자금이 달라질 수 있습니다.', next_check: '실제 점포 조건을 입력합니다.' }],
      risks: [{ risk_id: 'ui-only-risk', severity: 'MEDIUM', summary: '실제 임대 조건이 아직 반영되지 않았습니다.', evidence_refs: [] }],
      counterfactuals: [{ variable: 'rent', condition: '월 임차료가 20% 높아질 경우', decision_impact: '손익분기 매출이 상승합니다.' }],
      next_actions: ['실제 점포 임대 조건 확인', '장비 견적 확인'],
      decision_inputs: [
        { field: 'own_funds_krw', label: '현재 자기자금', value: 80_000_000, provenance: 'USER_INPUT', resolution_status: 'USER_CONFIRMED_FACT', decision_role: 'CONSTRAINT_INPUT', source: null, applied_to: ['CAPITAL'], replaceable_by: [], limitation_code: null, resolution_action: { type: 'USER_INPUT', target_fields: ['own_funds_krw'] } },
        { field: 'monthly_occupancy_krw', label: '지역 임차비 참고값', range: { currency: 'KRW', low: 1_700_000, base: 2_100_000, high: 2_500_000, provenance_refs: ['ui-only-rent-benchmark'] }, provenance: 'BENCHMARK', resolution_status: 'RESOLVED_BENCHMARK', decision_role: 'FINANCE_INPUT', source: { title: '한국부동산원 상업용부동산 임대동향조사 · UI 예시', source_ref: 'https://www.reb.or.kr', data_date: '2026-06-30', geographic_scope: '수원시 영통구' }, applied_to: ['INITIAL_CASH', 'MONTHLY_FIXED_COST'], replaceable_by: ['PROPERTY_TERMS'], limitation_code: 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY', resolution_action: { type: 'PROPERTY_TERMS', target_fields: ['deposit_krw', 'monthly_rent_krw', 'management_fee_krw', 'key_money_krw'] } },
        { field: 'equipment_cost_krw', label: '장비비', range: { currency: 'KRW', low: 12_000_000, base: 15_000_000, high: 18_000_000, provenance_refs: ['ui-only-assumption'] }, provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', decision_role: 'FINANCE_INPUT', source: null, applied_to: ['INITIAL_CASH'], replaceable_by: ['EQUIPMENT_QUOTE'], limitation_code: null, resolution_action: { type: 'DOCUMENT_INTAKE', target_fields: ['equipment_cost_krw'], accepted_document_types: ['EQUIPMENT_QUOTE'] } },
        { field: 'construction_cost_krw', label: '인테리어비', range: { currency: 'KRW', low: 18_000_000, base: 22_000_000, high: 28_000_000, provenance_refs: ['ui-only-assumption'] }, provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', decision_role: 'FINANCE_INPUT', source: null, applied_to: ['INITIAL_CASH'], replaceable_by: ['INTERIOR_QUOTE'], limitation_code: 'DEV_PREVIEW_BACKEND_NOT_WIRED', resolution_action: { type: 'DOCUMENT_INTAKE', target_fields: ['construction_cost_krw'], accepted_document_types: ['INTERIOR_QUOTE'] } },
      ],
      decision_trace: { gates: [{ gate_type: 'CAPITAL', status: 'PASS', reason_code: 'CURRENT_CONSTRAINTS_SATISFIED', decisive_input_refs: ['own_funds_krw', 'monthly_occupancy_krw'], metrics: { own_funds_krw: 80_000_000, minimum_required_krw: 55_000_000, remaining_at_minimum_krw: 25_000_000 } }] },
      rank_trace: { basis: 'NEXT_REVIEW_PRIORITY', factors: [{ code: 'INITIAL_CASH_BASE', value: 65_000_000 }, { code: 'MONTHLY_FIXED_COST_BASE', value: 3_800_000 }], decisive_factor: 'INITIAL_CASH_BASE' },
      verification_requirements: [],
    },
    {
      candidate_id: 'ui-only-franchise',
      project_id: project.project_id,
      state_version: 1,
      case_type: 'FRANCHISE',
      display_name: '예시 프랜차이즈 후보',
      review_status: 'REVIEW_RECOMMENDED',
      reason_codes: ['CURRENT_CONSTRAINTS_SATISFIED'],
      summary: '경제 계산은 완료됐고 특정 주소 출점 승인은 본사 확인으로 분리된 UI fixture입니다.',
      rank: 2,
      rank_basis: 'UI_ONLY_FIXTURE',
      is_primary_next_review: false,
      franchise: { brand_id: 'ui-only-brand', eligibility: 'VERIFIED', availability_status: 'HQ_CONFIRMATION_REQUIRED', eligibility_evidence_refs: ['ui-only-franchise-evidence'], disclosure_evidence_refs: [] },
      independent_model: null,
      evidence_refs: ['ui-only-franchise-evidence'],
      assumption_refs: [],
      market_signals: [],
      official_documents: [],
      official_document_gaps: ['정보공개서 공식 문서'],
      financial_summary: {
        initial_cash: { currency: 'KRW', low: 70_000_000, base: 82_000_000, high: 95_000_000, provenance_refs: ['ui-only-franchise-evidence'] },
        monthly_fixed_cost: { currency: 'KRW', low: 4_000_000, base: 5_000_000, high: 6_000_000, provenance_refs: ['ui-only-franchise-evidence'] },
        break_even_monthly_sales_krw: 16_000_000,
        required_daily_orders: 90,
        unknown_cost_fields: [],
      },
      missing_fields: [],
      risks: [{ risk_id: 'ui-only-hq-risk', severity: 'HIGH', summary: '희망 지역 출점 가능 여부 확인이 필요합니다.', evidence_refs: [] }],
      counterfactuals: [],
      next_actions: ['본사 출점 가능 여부 확인'],
      decision_inputs: [
        { field: 'franchise_initial_cost_krw', label: '가맹 초기비용', range: { currency: 'KRW', low: 70_000_000, base: 82_000_000, high: 95_000_000, provenance_refs: ['ui-only-franchise-evidence'] }, provenance: 'FACT', resolution_status: 'RESOLVED_FACT', decision_role: 'FINANCE_INPUT', source: { title: '예시 브랜드 정보공개서 · UI 예시', source_ref: null, data_date: '2026-05-31', geographic_scope: '브랜드 공통' }, applied_to: ['INITIAL_CASH'], replaceable_by: [], limitation_code: null, resolution_action: null },
        { field: 'franchise_initial_fees_krw', label: '가맹비·교육비·보증금', range: { currency: 'KRW', low: 8_000_000, base: 11_000_000, high: 14_000_000, provenance_refs: ['ui-only-franchise-assumption'] }, provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', decision_role: 'FINANCE_INPUT', source: null, applied_to: ['INITIAL_CASH'], replaceable_by: ['FRANCHISE_DISCLOSURE', 'FRANCHISE_AGREEMENT'], limitation_code: 'DEV_PREVIEW_BACKEND_NOT_WIRED', resolution_action: { type: 'DOCUMENT_INTAKE', target_fields: ['franchise_initial_fees_krw'], accepted_document_types: ['FRANCHISE_DISCLOSURE', 'FRANCHISE_AGREEMENT'] } },
        { field: 'royalty', label: '로열티 참고 가정', value: '매출의 3%', provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', decision_role: 'FINANCE_INPUT', source: null, applied_to: ['BREAK_EVEN'], replaceable_by: ['FRANCHISE_DISCLOSURE'], limitation_code: 'DEV_PREVIEW_BACKEND_NOT_WIRED', resolution_action: { type: 'DOCUMENT_INTAKE', target_fields: ['royalty'], accepted_document_types: ['FRANCHISE_DISCLOSURE', 'FRANCHISE_AGREEMENT'] } },
      ],
      decision_trace: { gates: [{ gate_type: 'CAPITAL', status: 'PASS', reason_code: 'CURRENT_CONSTRAINTS_SATISFIED', decisive_input_refs: ['franchise_initial_cost_krw'], metrics: { own_funds_krw: 80_000_000, minimum_required_krw: 70_000_000, remaining_at_minimum_krw: 10_000_000 } }] },
      rank_trace: { basis: 'NEXT_REVIEW_PRIORITY', factors: [{ code: 'INITIAL_CASH_BASE', value: 82_000_000 }], decisive_factor: 'INITIAL_CASH_BASE' },
      verification_requirements: [{ requirement_code: 'HQ_AREA_APPROVAL', label: '이 주소의 출점 가능 여부', resolver: 'FRANCHISE_HQ', authority: '브랜드 본사', current_status: 'EXTERNAL_CONFIRMATION_REQUIRED', required_evidence: ['본사 서면 확인'], reason: '특정 주소의 출점 승인 여부는 CaffeMate가 확정할 수 없습니다.', resolution_action: { type: 'EXTERNAL_CONFIRMATION', target_fields: ['franchise_area_approval'] } }],
    },
    {
      candidate_id: 'ui-only-seated', project_id: project.project_id, state_version: 1, case_type: 'INDEPENDENT',
      display_name: '소형 좌석 균형형 개인카페', review_status: 'REVIEW_RECOMMENDED', reason_codes: ['CURRENT_CONSTRAINTS_SATISFIED'],
      summary: '포장형보다 좌석 운영 비중이 높은 비교용 창업안입니다.', rank: 3, rank_basis: 'UI_ONLY_FIXTURE', is_primary_next_review: false,
      franchise: null, independent_model: { model_id: 'small-balanced-seating', adjusted_fields: [] }, evidence_refs: ['ui-only-evidence'], assumption_refs: ['ui-only-assumption'],
      market_signals: [], official_documents: [], official_document_gaps: [],
      financial_summary: { initial_cash: { currency: 'KRW', low: 68_000_000, base: 76_000_000, high: 86_000_000, provenance_refs: ['ui-only-rent-benchmark'] }, monthly_fixed_cost: { currency: 'KRW', low: 3_600_000, base: 4_400_000, high: 5_100_000, provenance_refs: ['ui-only-rent-benchmark'] }, break_even_monthly_sales_krw: 14_500_000, required_daily_orders: 78, unknown_cost_fields: [] },
      missing_fields: [], risks: [], counterfactuals: [{ variable: 'monthly_occupancy', condition: '실제 임차비가 참고 범위 상단을 넘을 경우', decision_impact: '초기자금과 손익분기 계산이 불리해질 수 있습니다.' }], next_actions: ['실제 점포 임대 조건 확인'],
      decision_inputs: [{ field: 'monthly_occupancy_krw', label: '지역 임차비 참고값', range: { currency: 'KRW', low: 2_000_000, base: 2_500_000, high: 3_000_000, provenance_refs: ['ui-only-rent-benchmark'] }, provenance: 'BENCHMARK', resolution_status: 'RESOLVED_BENCHMARK', decision_role: 'FINANCE_INPUT', source: { title: '한국부동산원 상업용부동산 임대동향조사 · UI 예시', source_ref: 'https://www.reb.or.kr', data_date: '2026-06-30', geographic_scope: '수원시 영통구' }, applied_to: ['INITIAL_CASH', 'MONTHLY_FIXED_COST'], replaceable_by: ['PROPERTY_TERMS'], limitation_code: 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY', resolution_action: { type: 'PROPERTY_TERMS', target_fields: ['deposit_krw', 'monthly_rent_krw', 'management_fee_krw'] } }],
      decision_trace: { gates: [{ gate_type: 'CAPITAL', status: 'PASS', reason_code: 'CURRENT_CONSTRAINTS_SATISFIED', decisive_input_refs: ['monthly_occupancy_krw'], metrics: { own_funds_krw: 80_000_000, minimum_required_krw: 68_000_000, remaining_at_minimum_krw: 12_000_000 } }] },
      rank_trace: { basis: 'NEXT_REVIEW_PRIORITY', factors: [{ code: 'INITIAL_CASH_BASE', value: 76_000_000 }], decisive_factor: 'INITIAL_CASH_BASE' }, verification_requirements: [],
    },
  ],
}

export function createUiOnlyDependencies(): {
  authGateway: AuthGateway
  apiFactory: (session: AuthSession) => ControlApiClient
} {
  const session: AuthSession = {
    uid: 'ui-only-user',
    displayName: 'UI 미리보기',
    getIdToken: async () => 'ui-only-token',
    signOut: async () => undefined,
  }

  const authGateway: AuthGateway = {
    restoreSession: async () => null,
    signIn: async () => session,
  }

  let currentProject = { ...project, state: null } as Project
  let currentResult = initialResult
  let selectedCandidateId = initialResult.primary_candidate_id ?? initialResult.candidates[0].candidate_id
  let feedbackStatus: FeedbackPreview['status'] = 'REVIEW_REQUIRED'
  let documentSequence = 0
  let currentDocumentType: DocumentType = 'PROPERTY_LISTING'
  let currentDocumentRevision: DocumentRevision | null = null
  let currentDocumentForm: DocumentExtractionForm | null = null

  const extractionFieldsFor = (documentType: DocumentType): DocumentExtractionForm['fields'] => {
    const field = (
      field_id: string,
      claim_type: string,
      label: string,
      value: string | number | boolean | null,
      unit: string | null,
      section: string,
    ) => ({
      field_id, claim_type, label, raw_value_text: value == null ? null : String(value),
      extracted_value: value, current_value: value, unit, materiality: 'HIGH',
      extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const,
      anchor: { page_index: 0, section_path: section }, warnings: [],
    })
    if (documentType === 'PROPERTY_LISTING') return [
      field('address', 'ADDRESS', '점포 주소', '서울 마포구 공덕동 1-1 · 개발 미리보기', null, '매물 정보'),
      field('area', 'AREA', '면적', 35, '㎡', '매물 정보'),
      field('floor', 'FLOOR', '층', '1층', null, '매물 정보'),
      field('deposit', 'LEASE_DEPOSIT', '보증금', 40_000_000, '원', '임대 조건'),
      field('rent', 'MONTHLY_RENT', '월세', 2_100_000, '원', '임대 조건'),
      field('management', 'MANAGEMENT_FEE', '관리비', 150_000, '원', '임대 조건'),
      field('key-money', 'KEY_MONEY', '권리금', 7_000_000, '원', '임대 조건'),
    ]
    if (documentType === 'COMMERCIAL_LEASE') return [
      field('area', 'AREA', '면적', 35, '㎡', '임대차 목적물'),
      field('floor', 'FLOOR', '층', '1층', null, '임대차 목적물'),
      field('deposit', 'LEASE_DEPOSIT', '보증금', 40_000_000, '원', '임대 조건'),
      field('rent', 'MONTHLY_RENT', '월세', 2_100_000, '원', '임대 조건'),
      field('management', 'MANAGEMENT_FEE', '관리비', 150_000, '원', '임대 조건'),
      field('key-money', 'KEY_MONEY', '권리금', 7_000_000, '원', '임대 조건'),
    ]
    if (documentType === 'EQUIPMENT_QUOTE') return [
      field('equipment_cost_krw', 'QUOTE_TOTAL', '장비 견적 총액', 13_000_000, '원', '견적 합계'),
    ]
    if (documentType === 'INTERIOR_QUOTE') return [
      field('construction_cost_krw', 'QUOTE_TOTAL', '인테리어 견적 총액', 20_000_000, '원', '공사비 합계'),
      field('interior_vat', 'VAT_STATUS', '부가세 포함 여부', '포함', null, '견적 조건'),
    ]
    if (documentType === 'FRANCHISE_DISCLOSURE' || documentType === 'FRANCHISE_AGREEMENT') return [
      field('franchise_initial_fees_krw', 'FRANCHISE_FEE', '가맹비·교육비·보증금', 10_000_000, '원', '가맹금'),
      field('royalty', 'ROYALTY', '로열티', '매출의 2.5%', null, '로열티'),
    ]
    return [field('material_fact', 'MATERIAL_FACT', '확인한 값', '개발 미리보기 문서 값', null, '본문')]
  }

  const makeExtractionForm = (revision: DocumentRevision): DocumentExtractionForm => ({
    form_id: `ui-only-form-${revision.revision_number}`,
    project_id: project.project_id,
    document_id: revision.document_id,
    document_revision_id: revision.document_revision_id,
    expected_state_version: currentResult.current_head.state_version,
    form_status: 'READY_FOR_REVIEW',
    fields: extractionFieldsFor(revision.document_type),
    apply_label: '반영하고 다시 계산',
    form_digest: `sha256:${String(revision.revision_number).padStart(64, 'a')}`,
    applied_state_version: null,
  })

  const applyUiOnlyDocumentResult = (form: DocumentExtractionForm) => {
    const selected = currentResult.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId) ?? currentResult.candidates[0]
    const previousSummary = selected.financial_summary
    const beforeInputs = selected.decision_inputs ?? []
    const updates = new Map(form.fields.map((entry) => [entry.field_id, entry.current_value]))
    const inputChanges: NonNullable<ResultView['decision_delta']>['candidate_changes'][number]['input_changes'] = []
    const decisionInputs = beforeInputs.map((input) => {
      if (!updates.has(input.field)) return input
      const nextValue = updates.get(input.field) ?? null
      const after = {
        ...input,
        value: nextValue,
        range: undefined,
        provenance: 'USER_INPUT' as const,
        resolution_status: 'USER_CONFIRMED_FACT' as const,
        source: { title: '개발 미리보기 업로드 문서', source_ref: null, data_date: '2026-08-25', geographic_scope: '선택 후보' },
        replaceable_by: [],
        limitation_code: null,
        resolution_action: null,
      }
      inputChanges.push({ field: input.field, before: input, after, applied_to: input.applied_to })
      return after
    })
    const initialDelta = ['EQUIPMENT_QUOTE', 'INTERIOR_QUOTE', 'FRANCHISE_DISCLOSURE', 'FRANCHISE_AGREEMENT'].includes(currentDocumentType)
      ? -2_000_000
      : 0
    const monthlyDelta = updates.has('royalty') ? 250_000 : 0
    const nextCandidate = {
      ...selected,
      state_version: selected.state_version + 1,
      decision_inputs: decisionInputs,
      financial_summary: {
        ...previousSummary,
        initial_cash: {
          ...previousSummary.initial_cash,
          low: previousSummary.initial_cash.low == null ? null : Math.max(0, previousSummary.initial_cash.low + initialDelta),
          base: previousSummary.initial_cash.base == null ? null : Math.max(0, previousSummary.initial_cash.base + initialDelta),
          high: previousSummary.initial_cash.high == null ? null : Math.max(0, previousSummary.initial_cash.high + initialDelta),
          provenance_refs: ['ui-only-uploaded-document'],
        },
        break_even_monthly_sales_krw: previousSummary.break_even_monthly_sales_krw == null
          ? null
          : previousSummary.break_even_monthly_sales_krw + monthlyDelta,
      },
    }
    const nextHead = { ...currentResult.current_head, state_version: currentResult.current_head.state_version + 1, workflow_generation: currentResult.current_head.workflow_generation + 1 }
    currentResult = {
      ...currentResult,
      result_bundle_id: `ui-only-result-document-${documentSequence}`,
      head: nextHead,
      current_head: nextHead,
      candidates: currentResult.candidates.map((candidate) => candidate.candidate_id === selected.candidate_id ? nextCandidate : candidate),
      decision_delta: {
        previous_result_bundle_id: currentResult.result_bundle_id,
        current_result_bundle_id: `ui-only-result-document-${documentSequence}`,
        primary_candidate_changed: false,
        requires_human_review: false,
        human_review_reason_codes: [],
        candidate_changes: [{
          candidate_key: `${selected.case_type}:${selected.independent_model?.model_id ?? selected.franchise?.brand_id ?? 'candidate'}`,
          display_name: selected.display_name,
          change_type: 'UPDATED',
          previous_rank: selected.rank,
          current_rank: selected.rank,
          previous_review_status: selected.review_status,
          current_review_status: selected.review_status,
          initial_cash_base_delta_krw: initialDelta,
          monthly_fixed_cost_base_delta_krw: 0,
          break_even_monthly_sales_delta_krw: monthlyDelta,
          reason_codes_added: [],
          reason_codes_removed: [],
          input_changes: inputChanges,
          gate_changes: [],
        }],
      },
    }
  }

  const apiFactory = (): ControlApiClient => ({
    createProject: async () => currentProject,
    listProjects: async () => [],
    searchAreas: async (_projectId, query): Promise<AreaSearchResult> => ({
      query,
      status: 'OK',
      completeness: 'UNVERIFIED',
      candidates: [{
        area_id: 'legal-dong:4111710300',
        scope_type: 'LEGAL_DONG',
        display_name: '경기도 수원시 영통구 원천동',
        legal_dong_code: '4111710300',
        administrative_dong_codes: [],
        mapping_status: 'UNVERIFIED',
        source_revision: 'UI_ONLY_FIXTURE',
        boundary_version: null,
        selection_token: 'ui-only-area-token',
      }],
      missing_fields: [],
      source_trace: [],
    }),
    confirmOnboarding: async () => {
      currentProject = project
      return project
    },
    startFirstProposal: async () => workflow,
    getWorkflow: async () => progress,
    getResult: async () => currentResult,
    explainResult: async (_projectId, _result, question, candidateId): Promise<ResultExplanation> => ({
      explanation_id: 'ui-only-explanation',
      result_bundle_id: currentResult.result_bundle_id,
      candidate_id: candidateId ?? currentResult.primary_candidate_id ?? currentResult.candidates[0].candidate_id,
      intent: 'WHY_RECOMMENDED',
      conclusion: 'UI 전용 fixture를 바탕으로 표시한 설명입니다.',
      reasons: ['화면 상태와 컴포넌트 배치를 확인하기 위한 예시입니다.'],
      evidence: [],
      unknowns: ['실제 데이터는 연결되어 있지 않습니다.'],
      decision_change_conditions: [],
      suggested_action: /바꿔|제외|변경/.test(question) ? 'OPEN_CONDITION_CHANGE' : 'NONE',
      state_changed: false,
    }),
    createFeedbackPreview: async (_projectId, input): Promise<FeedbackPreview> => ({
      preview_id: 'ui-only-feedback',
      project_id: project.project_id,
      result_bundle_id: initialResult.result_bundle_id,
      head,
      status: feedbackStatus,
      latest_user_input: input,
      before_founder: { own_funds_krw: 80_000_000 },
      after_founder: { own_funds_krw: 70_000_000 },
      operations: [],
      clarifying_questions: [],
      affected_stage_codes: ['RUN_PROPOSAL'],
      risk_flags: [],
      proposal_digest: `sha256:${'a'.repeat(64)}`,
    }),
    confirmFeedback: async (_projectId, preview): Promise<FeedbackResolution> => {
      feedbackStatus = 'CONFIRMED'
      return { preview: { ...preview, status: 'CONFIRMED' }, state_version: 2, workflow }
    },
    cancelFeedback: async (_projectId, previewId): Promise<FeedbackResolution> => {
      feedbackStatus = 'CANCELLED'
      return {
        preview: {
          preview_id: previewId,
          project_id: project.project_id,
          result_bundle_id: initialResult.result_bundle_id,
          head,
          status: 'CANCELLED',
          latest_user_input: '',
          before_founder: {},
          after_founder: null,
          operations: [],
          clarifying_questions: [],
          affected_stage_codes: [],
          risk_flags: [],
          proposal_digest: null,
        },
        state_version: null,
        workflow: null,
      }
    },
    selectCandidate: async (_projectId, _result, candidateId) => {
      selectedCandidateId = candidateId
      return {
        selection_id: 'ui-only-selection', candidate_id: candidateId, selected_state_version: 1,
        required_evidence: [], property_intake_enabled: true, document_intake_enabled: true,
      }
    },
    getPreparationGuide: async (): Promise<PreparationGuide> => ({
      project_id: project.project_id,
      selection_id: 'ui-only-selection',
      candidate_id: selectedCandidateId,
      candidate_type: currentResult.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId)?.case_type ?? 'INDEPENDENT',
      jurisdiction_code: '4111756000',
      jurisdiction_display_name: '경기도 수원시 영통구 원천동',
      as_of: '2026-08-25',
      status: 'REVIEW_REQUIRED',
      procedures: [
        {
          procedure_type: 'HYGIENE_EDUCATION', status: 'OK', missing_fields: [], conflicts: [], error_codes: [],
          steps: [{ procedure_type: 'HYGIENE_EDUCATION', step_order: 1, title: '신규 영업자 위생교육 이수', required: true, authority: '식품위생교육기관', source_date: '2026-08-25', evidence_id: 'ui-only-procedure-hygiene' }],
        },
        {
          procedure_type: 'FOOD_SERVICE_REPORT', status: 'PARTIAL', missing_fields: ['facility_check'], conflicts: [], error_codes: [],
          steps: [{ procedure_type: 'FOOD_SERVICE_REPORT', step_order: 1, title: '휴게음식점 영업신고 준비', required: true, authority: '관할 구청 위생 담당 부서', source_date: '2026-08-25', evidence_id: 'ui-only-procedure-report' }],
        },
      ],
      human_actions_only: true,
      external_submission_performed: false,
      generated_at: now,
    }),
    applyPropertyTerms: async (_projectId, selectionId, _expectedStateVersion, terms) => {
      const selected = currentResult.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId) ?? currentResult.candidates[0]
      const previousInput = selected.decision_inputs?.find((input) => input.resolution_action?.type === 'PROPERTY_TERMS') ?? null
      const actualInput = {
        field: 'actual_property_terms', label: '실제 점포 임대 조건', value: `${terms.monthly_rent_krw.toLocaleString('ko-KR')}원/월`,
        provenance: 'USER_INPUT' as const, resolution_status: 'USER_CONFIRMED_FACT' as const, decision_role: 'FINANCE_INPUT' as const,
        source: null, applied_to: ['INITIAL_CASH', 'MONTHLY_FIXED_COST'], replaceable_by: [], limitation_code: null,
        resolution_action: { type: 'PROPERTY_TERMS' as const, target_fields: ['deposit_krw', 'monthly_rent_krw', 'management_fee_krw', 'key_money_krw'] },
      }
      const recalculatedCandidate = {
        ...selected,
        candidate_id: `${selected.candidate_id}-property`, state_version: 2, review_status: 'EXCLUDED' as const,
        reason_codes: ['MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS'], rank: null, rank_basis: 'NOT_RANKED', is_primary_next_review: false,
        summary: '실제 점포 임대 조건을 반영하니 현재 자기자금 범위를 넘었습니다.',
        financial_summary: {
          ...selected.financial_summary,
          initial_cash: { currency: 'KRW' as const, low: 85_000_000, base: 92_000_000, high: 101_000_000, provenance_refs: ['ui-only-property'] },
          monthly_fixed_cost: { currency: 'KRW' as const, low: 4_800_000, base: 5_200_000, high: 5_700_000, provenance_refs: ['ui-only-property'] },
        },
        decision_inputs: [...(selected.decision_inputs ?? []).filter((input) => input !== previousInput), actualInput],
        decision_trace: { gates: [{ gate_type: 'CAPITAL', status: 'FAIL' as const, reason_code: 'MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS', decisive_input_refs: ['actual_property_terms'], metrics: { own_funds_krw: 80_000_000, minimum_required_krw: 85_000_000, shortfall_krw: 5_000_000 } }] },
        rank_trace: null,
      }
      currentResult = {
        ...currentResult,
        result_bundle_id: 'ui-only-result-property', primary_candidate_id: null, outcome_status: 'NO_REVIEWABLE_CANDIDATES',
        candidates: [recalculatedCandidate], head: { ...head, state_version: 2, workflow_generation: 2 }, current_head: { ...head, state_version: 2, workflow_generation: 2 },
        decision_delta: {
          previous_result_bundle_id: initialResult.result_bundle_id, current_result_bundle_id: 'ui-only-result-property', primary_candidate_changed: true,
          requires_human_review: false, human_review_reason_codes: [],
          candidate_changes: [{ candidate_key: `${selected.case_type}:${selected.independent_model?.model_id ?? selected.franchise?.brand_id ?? 'candidate'}`, display_name: selected.display_name, change_type: 'UPDATED', previous_rank: selected.rank, current_rank: null, previous_review_status: selected.review_status, current_review_status: 'EXCLUDED', initial_cash_base_delta_krw: 27_000_000, monthly_fixed_cost_base_delta_krw: 1_400_000, break_even_monthly_sales_delta_krw: null, reason_codes_added: ['MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS'], reason_codes_removed: [], input_changes: [{ field: 'actual_property_terms', before: previousInput, after: actualInput, applied_to: ['INITIAL_CASH', 'MONTHLY_FIXED_COST'] }], gate_changes: [{ gate_type: 'CAPITAL', previous_status: 'PASS', current_status: 'FAIL', reason_code: 'MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS' }] }],
        },
      }
      return {
        property_input_id: 'ui-only-property', project_id: project.project_id, selection_id: selectionId, candidate_id: selectedCandidateId,
        applied_state_version: 2, terms, previous_financial_summary: selected.financial_summary,
        recompute_workflow: { ...workflow, workflow_run_id: 'ui-only-property-workflow', head: { ...head, state_version: 2, workflow_generation: 2 } },
        input_kind: 'USER_CONFIRMED_PROPERTY_TERMS' as const, is_demo_fixture: true, created_at: now,
      }
    },
    beginDocumentUpload: async (_projectId, file, documentType): Promise<SignedDocumentUpload> => {
      documentSequence += 1
      currentDocumentType = documentType
      const documentId = `ui-only-document-${documentSequence}`
      const revisionId = `ui-only-revision-${documentSequence}`
      currentDocumentRevision = {
        document_id: documentId,
        document_revision_id: revisionId,
        project_id: project.project_id,
        revision_number: documentSequence,
        document_type: documentType,
        original_filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
        sha256: 'a'.repeat(64),
        status: 'UPLOAD_PENDING',
        failure_codes: [],
        created_at: now,
        updated_at: now,
        completed_at: null,
      }
      currentDocumentForm = null
      return {
        document_id: documentId,
        document_revision_id: revisionId,
        revision_number: documentSequence,
        object_path: `ui-only/${documentId}/${file.name}`,
        upload_url: 'ui-only://upload',
        method: 'PUT',
        required_headers: { 'Content-Type': file.type },
        expires_at: '2026-08-25T23:59:59Z',
        status: 'UPLOAD_PENDING',
      }
    },
    uploadDocument: async () => undefined,
    completeDocumentUpload: async (_projectId, revisionId): Promise<DocumentRevision> => {
      if (!currentDocumentRevision || currentDocumentRevision.document_revision_id !== revisionId) throw new Error('UI_ONLY_DOCUMENT_NOT_FOUND')
      currentDocumentRevision = { ...currentDocumentRevision, status: 'EXTRACTION_READY', updated_at: now, completed_at: now }
      currentDocumentForm = makeExtractionForm(currentDocumentRevision)
      return currentDocumentRevision
    },
    getDocumentRevision: async (_projectId, revisionId): Promise<DocumentRevision> => {
      if (!currentDocumentRevision || currentDocumentRevision.document_revision_id !== revisionId) throw new Error('UI_ONLY_DOCUMENT_NOT_FOUND')
      return currentDocumentRevision
    },
    getDocumentExtractionForm: async (_projectId, revisionId): Promise<DocumentExtractionForm> => {
      if (!currentDocumentForm || currentDocumentForm.document_revision_id !== revisionId) throw new Error('UI_ONLY_EXTRACTION_NOT_READY')
      return currentDocumentForm
    },
    updateDocumentExtractionForm: async (_projectId, form, edits): Promise<DocumentExtractionForm> => {
      const editMap = new Map(edits.map((edit) => [edit.field_id, edit.value]))
      currentDocumentForm = {
        ...form,
        fields: form.fields.map((entry) => editMap.has(entry.field_id)
          ? { ...entry, current_value: editMap.get(entry.field_id) ?? null, edit_status: 'EDITED' as const }
          : entry),
        form_digest: `sha256:${'b'.repeat(64)}`,
      }
      return currentDocumentForm
    },
    applyDocumentExtractionForm: async (_projectId, form): Promise<ExtractionFormApplication> => {
      applyUiOnlyDocumentResult(form)
      currentDocumentForm = { ...form, form_status: 'APPLIED', applied_state_version: currentResult.current_head.state_version }
      return {
        application_id: `ui-only-application-${documentSequence}`,
        project_id: project.project_id,
        document_revision_id: form.document_revision_id,
        applied_state_version: currentResult.current_head.state_version,
        recompute_workflow_run_id: `ui-only-document-workflow-${documentSequence}`,
        claims: [],
        conflicts: [],
        requires_human_review: false,
      }
    },
  })

  return { authGateway, apiFactory }
}
