import type { AuthGateway, AuthSession } from './auth'
import type {
  AreaSearchResult,
  ControlApiClient,
  FeedbackPreview,
  FeedbackResolution,
  HeadFence,
  PreparationGuide,
  Project,
  ResultExplanation,
  ResultView,
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
  completed_stage_count: 1,
  total_stage_count: 1,
  current_stage_codes: [],
  terminal_reason_codes: [],
  human_review_requests: [],
  poll_after_ms: null,
}

const result: ResultView = {
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
        { signal_type: 'CAFE_COUNT', value: 42, unit: 'STORES', data_date: '2026-06-30', freshness_status: 'FRESH', source_title: 'UI 전용 예시 데이터', source_ref: 'ui-only://market/cafes', evidence_id: 'ui-only-market-cafes', caveat: '화면 확인을 위한 로컬 fixture입니다.' },
        { signal_type: 'FOOT_TRAFFIC', value: 185000, unit: 'PERSON_VISITS_PER_MONTH_ESTIMATE', data_date: '2026-06-30', freshness_status: 'FRESH', source_title: 'UI 전용 예시 데이터', source_ref: 'ui-only://market/traffic', evidence_id: 'ui-only-market-traffic', caveat: '화면 확인을 위한 로컬 fixture입니다.' },
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
    },
    {
      candidate_id: 'ui-only-franchise',
      project_id: project.project_id,
      state_version: 1,
      case_type: 'FRANCHISE',
      display_name: '예시 프랜차이즈 후보',
      review_status: 'CONDITIONAL_REVIEW',
      reason_codes: ['HQ_CONFIRMATION_REQUIRED'],
      summary: '프랜차이즈 카드와 조건부 상태를 확인하기 위한 UI fixture입니다.',
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
        unknown_cost_fields: ['royalty'],
      },
      missing_fields: [{ field: 'royalty', impact: '월 고정비가 달라질 수 있습니다.', next_check: '본사에 확인합니다.' }],
      risks: [{ risk_id: 'ui-only-hq-risk', severity: 'HIGH', summary: '희망 지역 출점 가능 여부 확인이 필요합니다.', evidence_refs: [] }],
      counterfactuals: [],
      next_actions: ['본사 출점 가능 여부 확인'],
    },
  ],
}

function unsupported(): Promise<never> {
  return Promise.reject(new Error('UI_ONLY_UNSUPPORTED'))
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
  let feedbackStatus: FeedbackPreview['status'] = 'REVIEW_REQUIRED'

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
    getResult: async () => result,
    explainResult: async (_projectId, _result, _question, candidateId): Promise<ResultExplanation> => ({
      explanation_id: 'ui-only-explanation',
      result_bundle_id: result.result_bundle_id,
      candidate_id: candidateId ?? result.primary_candidate_id!,
      intent: 'WHY_RECOMMENDED',
      conclusion: 'UI 전용 fixture를 바탕으로 표시한 설명입니다.',
      reasons: ['화면 상태와 컴포넌트 배치를 확인하기 위한 예시입니다.'],
      evidence: [],
      unknowns: ['실제 데이터는 연결되어 있지 않습니다.'],
      decision_change_conditions: [],
      suggested_action: 'NONE',
      state_changed: false,
    }),
    createFeedbackPreview: async (_projectId, input): Promise<FeedbackPreview> => ({
      preview_id: 'ui-only-feedback',
      project_id: project.project_id,
      result_bundle_id: result.result_bundle_id,
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
          result_bundle_id: result.result_bundle_id,
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
    selectCandidate: async (_projectId, _result, candidateId) => ({
      selection_id: 'ui-only-selection',
      candidate_id: candidateId,
      selected_state_version: 1,
      required_evidence: [{ code: 'LEASE', title: '점포 임대 조건', status: 'REQUIRED', reason: 'UI 확인용 필수 항목입니다.' }],
      property_intake_enabled: true,
      document_intake_enabled: true,
    }),
    getPreparationGuide: async (): Promise<PreparationGuide> => ({
      project_id: project.project_id,
      selection_id: 'ui-only-selection',
      candidate_id: result.primary_candidate_id!,
      candidate_type: 'INDEPENDENT',
      jurisdiction_code: '4111756000',
      jurisdiction_display_name: '경기도 수원시 영통구 원천동',
      as_of: '2026-08-25',
      status: 'REVIEW_REQUIRED',
      procedures: [],
      human_actions_only: true,
      external_submission_performed: false,
      generated_at: now,
    }),
    applyPropertyTerms: async (_projectId, selectionId, _expectedStateVersion, terms) => ({
      property_input_id: 'ui-only-property',
      project_id: project.project_id,
      selection_id: selectionId,
      candidate_id: result.primary_candidate_id!,
      applied_state_version: 2,
      terms,
      previous_financial_summary: result.candidates[0].financial_summary,
      recompute_workflow: workflow,
      input_kind: 'USER_CONFIRMED_PROPERTY_TERMS',
      is_demo_fixture: true,
      created_at: now,
    }),
    beginDocumentUpload: unsupported,
    uploadDocument: unsupported,
    completeDocumentUpload: unsupported,
    getDocumentRevision: unsupported,
    getDocumentExtractionForm: unsupported,
    updateDocumentExtractionForm: unsupported,
    applyDocumentExtractionForm: unsupported,
  })

  return { authGateway, apiFactory }
}
