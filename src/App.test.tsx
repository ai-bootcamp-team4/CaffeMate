import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AuthGateway, AuthSession } from './auth'
import type { AreaSearchResult, ControlApiClient, HeadFence, PreparationGuide, Project, ResultView, WorkflowProgress } from './apiClient'

afterEach(cleanup)

const head: HeadFence = {
  workflow_generation: 1,
  state_version: 1,
  founder_snapshot_id: 'founder-1',
  area_snapshot_id: 'area-1',
  evidence_snapshot_id: 'evidence-1',
  policy_snapshot_id: 'policy-v1',
  index_generation_id: 'index-1',
  seed_registry_id: 'seed-1',
}

const project: Project = {
  project_id: 'project-1',
  user_id: 'user-1',
  created_at: '2026-08-22T00:00:00Z',
  state: {
    state_version: 1,
    status: 'ANALYZING',
    founder: { own_funds_krw: 50_000_000, borrowing_intent: 'UNDECIDED' },
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

const result: ResultView = {
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
    case_type: 'FRANCHISE', display_name: '실제 검증 브랜드', review_status: 'CONDITIONAL_REVIEW',
    reason_codes: ['HQ_CONFIRMATION_REQUIRED'], summary: '출점 가능 여부 확인이 필요한 조건부 후보입니다.',
    rank: 1, rank_basis: 'NEXT_REVIEW_PRIORITY', is_primary_next_review: true,
    franchise: { brand_id: 'brand-1', eligibility: 'VERIFIED', availability_status: 'HQ_CONFIRMATION_REQUIRED', eligibility_evidence_refs: ['evidence-franchise'], disclosure_evidence_refs: [] },
    independent_model: null, evidence_refs: ['evidence-franchise'], assumption_refs: ['assumption-rent'],
    market_signals: [
      { signal_type: 'CAFE_COUNT', value: 208, unit: 'STORES', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do', evidence_id: 'evidence-market-cafes', caveat: '선택 지역에 연결된 행정동의 카페 업종 집계이며 개별 점포의 경쟁력을 뜻하지 않습니다.' },
      { signal_type: 'ESTIMATED_SALES', value: 2_596_733_728, unit: 'KRW_PER_QUARTER_ESTIMATE', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-15572/A/1/datasetView.do', evidence_id: 'evidence-market-sales', caveat: '선택 지역의 카페 업종 분기 추정매출 합계이며 신규 점포 예상매출이 아닙니다.' },
      { signal_type: 'FOOT_TRAFFIC', value: 12_465_323, unit: 'PERSON_VISITS_PER_QUARTER_ESTIMATE', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do', evidence_id: 'evidence-market-foot', caveat: '선택 지역의 분기 추정 유동인구이며 고유 방문자 수가 아닙니다.' },
      { signal_type: 'RESIDENT_POPULATION', value: 37_068, unit: 'PERSONS', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-22182/S/1/datasetView.do', evidence_id: 'evidence-market-resident', caveat: '선택 지역에 연결된 행정동의 거주인구 합계입니다.' },
      { signal_type: 'WORKER_POPULATION', value: 7_365, unit: 'PERSONS', data_date: '2026-03-31', freshness_status: 'FRESH', source_title: '서울시 상권분석서비스', source_ref: 'https://data.seoul.go.kr/dataList/OA-22184/A/1/datasetView.do', evidence_id: 'evidence-market-worker', caveat: '선택 지역에 연결된 행정동의 직장인구 합계입니다.' },
    ],
    financial_summary: {
      initial_cash: { currency: 'KRW', low: 70_000_000, base: 80_000_000, high: 90_000_000, provenance_refs: ['evidence-cost'] },
      monthly_fixed_cost: { currency: 'KRW', low: 4_000_000, base: 5_000_000, high: 6_000_000, provenance_refs: ['evidence-cost'] },
      break_even_monthly_sales_krw: 15_000_000, required_daily_orders: 80, unknown_cost_fields: ['premium'],
    },
    missing_fields: [{ field: 'royalty', impact: '월 고정비가 바뀝니다.', next_check: '본사에 확인합니다.' }],
    risks: [
      { risk_id: 'risk-1', severity: 'HIGH', summary: '출점 가능 여부가 확인되지 않았습니다.', evidence_refs: [] },
      { risk_id: 'risk-2', severity: 'HIGH', summary: '출점 가능 여부가 확인되지 않았습니다.', evidence_refs: [] },
    ],
    counterfactuals: [{ variable: 'rent', condition: '월세 15% 감소', decision_impact: '검토 우선순위가 상승합니다.' }],
    next_actions: ['본사 출점 가능 여부 확인'],
  }],
}

const workflow = { workflow_run_id: 'workflow-1', project_id: 'project-1', workflow_code: 'FIRST_PROPOSAL' as const, status: 'SUCCEEDED' as const, head, created_at: '2026-08-22T00:01:00Z', updated_at: '2026-08-22T00:02:00Z' }
const progress: WorkflowProgress = { ...workflow, completed_stage_count: 9, total_stage_count: 9, current_stage_codes: [], terminal_reason_codes: [], human_review_requests: [], poll_after_ms: null }
const preparationGuide: PreparationGuide = {
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

function setup(nextResult: ResultView = result) {
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

async function enterOnboarding() {
  fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
  await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })
}

async function completeOnboarding() {
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

describe('CaffeMate Control API integration', () => {
  it('lists saved projects after sign-in and resumes a saved result without creating a project', async () => {
    const { client } = setup()
    vi.mocked(client.listProjects).mockResolvedValueOnce([project])

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))

    expect(await screen.findByRole('heading', { name: '이어서 살펴볼 카페 창업안을 선택하세요.' })).toBeTruthy()
    expect(screen.getByText('수원시 영통구 원천동')).toBeTruthy()
    expect(client.createProject).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '이어보기' }))

    expect(await screen.findByRole('heading', { name: '실제 검증 브랜드' })).toBeTruthy()
    expect(client.getResult).toHaveBeenCalledWith('project-1')
    expect(client.startFirstProposal).not.toHaveBeenCalled()
  })

  it('creates a separate project from the saved project catalogue', async () => {
    const { client } = setup()
    vi.mocked(client.listProjects).mockResolvedValueOnce([project])

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
    await screen.findByRole('heading', { name: '이어서 살펴볼 카페 창업안을 선택하세요.' })
    fireEvent.click(screen.getByRole('button', { name: '새 분석 만들기' }))

    expect(await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })).toBeTruthy()
    expect(client.createProject).toHaveBeenCalledOnce()
  })

  it('requires Google sign-in before creating a project', async () => {
    const { authGateway, client } = setup()
    await enterOnboarding()
    expect(authGateway.signIn).toHaveBeenCalledOnce()
    expect(client.createProject).toHaveBeenCalledOnce()
  })

  it('runs FIRST_PROPOSAL and renders only the returned result', async () => {
    const { client } = setup()
    await completeOnboarding()
    expect(client.confirmOnboarding).toHaveBeenCalledOnce()
    expect(client.confirmOnboarding).toHaveBeenCalledWith('project-1', expect.any(Object), 'signed-area-selection')
    expect(client.startFirstProposal).toHaveBeenCalledWith('project-1')
    expect(client.getWorkflow).toHaveBeenCalledWith('project-1', 'workflow-1')
    expect(screen.getByText('지금 예산에 맞는 운영안이나 실제 점포 비용으로 한 번 더 비교해 보세요.')).toBeTruthy()
    expect(screen.getAllByText(/70,000,000원/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/가상 목업값/)).toBeNull()
  })

  it('retries only FIRST_PROPOSAL after onboarding was already confirmed', async () => {
    const { client } = setup()
    vi.mocked(client.startFirstProposal)
      .mockRejectedValueOnce(new Error('분석 서비스를 준비하지 못했습니다.'))
      .mockResolvedValueOnce(workflow)

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

    expect(await screen.findByText('분석 서비스를 준비하지 못했습니다.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '분석 시작' }))

    expect(await screen.findByRole('heading', { name: '실제 검증 브랜드' })).toBeTruthy()
    expect(client.confirmOnboarding).toHaveBeenCalledOnce()
    expect(client.startFirstProposal).toHaveBeenCalledTimes(2)
  })

  it('renders the primary proposal and two comparison proposals returned by the API', async () => {
    const comparisonResult: ResultView = {
      ...result,
      candidates: [
        result.candidates[0],
        { ...result.candidates[0], candidate_id: 'candidate-2', display_name: '소형 포장 중심 개인카페', rank: 2, is_primary_next_review: false },
        { ...result.candidates[0], candidate_id: 'candidate-3', display_name: '좌석 중심 개인카페', rank: 3, is_primary_next_review: false },
      ],
    }
    setup(comparisonResult)
    await completeOnboarding()

    expect(screen.getByText('검토 후보 3개')).toBeTruthy()
    expect(screen.getByRole('tab', { name: /소형 포장 중심 개인카페/ })).toBeTruthy()
    expect(screen.getByRole('tab', { name: /좌석 중심 개인카페/ })).toBeTruthy()
  })

  it('persists an explicit next-preparation selection through the API', async () => {
    const { client } = setup()
    await completeOnboarding()
    fireEvent.click(screen.getByRole('button', { name: '이 안을 계속 검토하기' }))
    await waitFor(() => expect(client.selectCandidate).toHaveBeenCalledWith('project-1', result, 'candidate-1'))
    await waitFor(() => expect(client.getPreparationGuide).toHaveBeenCalledWith('project-1', 'selection-1'))
    expect(await screen.findByRole('heading', { name: '실제 검증 브랜드에 점포 조건을 넣어보세요' })).toBeTruthy()
    expect(screen.getByText('점포 임대 조건')).toBeTruthy()
    expect(screen.getByText('신규 영업자 위생교육 이수')).toBeTruthy()
    expect(screen.queryByText('다음 준비 완료')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '결과 비교로 돌아가기' }))
    expect(screen.getByRole('button', { name: '준비 자료 보기' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '선택한 안의 준비 자료 보기' })).toBeNull()
  })

  it('recalculates the selected candidate from six editable property terms', async () => {
    const recalculated: ResultView = {
      ...result,
      result_bundle_id: 'result-2',
      candidates: [{
        ...result.candidates[0],
        candidate_id: 'candidate-recalculated',
        financial_summary: {
          ...result.candidates[0].financial_summary,
          initial_cash: { currency: 'KRW', low: 60_000_000, base: 70_000_000, high: 80_000_000, provenance_refs: ['property-1'] },
          monthly_fixed_cost: { currency: 'KRW', low: 2_400_000, base: 2_400_000, high: 2_400_000, provenance_refs: ['property-1'] },
        },
      }],
    }
    const { client } = setup()
    await completeOnboarding()
    fireEvent.click(screen.getByRole('button', { name: '이 안을 계속 검토하기' }))
    await screen.findByRole('heading', { name: '실제 검증 브랜드에 점포 조건을 넣어보세요' })
    vi.mocked(client.getResult).mockResolvedValueOnce(recalculated)

    fireEvent.click(screen.getByRole('button', { name: '데모 입력 예시 불러오기' }))
    fireEvent.change(screen.getByLabelText('월세(만원)'), { target: { value: '200' } })
    fireEvent.click(screen.getByRole('button', { name: '이 조건으로 비용 다시 계산' }))

    await waitFor(() => expect(client.applyPropertyTerms).toHaveBeenCalledWith('project-1', 'selection-1', 2, expect.objectContaining({ monthly_rent_krw: 2_000_000, deposit_krw: 30_000_000 })))
    expect(await screen.findByRole('heading', { name: '임시값과 점포 반영값 비교' })).toBeTruthy()
    expect(screen.getByText('70,000,000원')).toBeTruthy()
  })

  it('keeps the selected checklist usable when official procedure lookup fails', async () => {
    const { client } = setup()
    vi.mocked(client.getPreparationGuide).mockRejectedValueOnce(new Error('temporary procedure lookup failure'))
    await completeOnboarding()

    fireEvent.click(screen.getByRole('button', { name: '이 안을 계속 검토하기' }))

    expect(await screen.findByRole('heading', { name: '실제 검증 브랜드에 점포 조건을 넣어보세요' })).toBeTruthy()
    expect(screen.getByText('점포 임대 조건')).toBeTruthy()
    expect(await screen.findByRole('button', { name: '다시 확인' })).toBeTruthy()
  })

  it('shows the funding gap first and prepares a smaller-model feedback request', async () => {
    setup()
    await completeOnboarding()

    expect(screen.getAllByText('지금 예산에는 조금 큰 안이에요').length).toBeGreaterThan(0)
    expect(screen.getByText(/최소 20,000,000원을 더 마련하거나/)).toBeTruthy()
    expect(screen.getByText('최소 부족액 20,000,000원')).toBeTruthy()
    expect(screen.queryByText(/감사 사람 확인 필요/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '예산에 맞는 작은 안 보기' }))
    await waitFor(() => expect((screen.getByLabelText('자연어 피드백') as HTMLTextAreaElement).value).toBe('현재 자기자금 범위에 더 가까운 작은 개인카페 운영안으로 다시 보고 싶어요.'))
  })

  it('keeps internal result codes and identifiers out of user-facing panels', async () => {
    setup()
    await completeOnboarding()

    expect(screen.queryByText('HQ_CONFIRMATION_REQUIRED')).toBeNull()
    expect(screen.queryByText('evidence-franchise')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: '상권 신호' }))
    expect(screen.queryByText('전국 기준 자료 없음')).toBeNull()
    expect(screen.queryByText(/행정동 연결 정보 · 점포 추정 매출/)).toBeNull()
    expect(screen.getByText('확인한 상권 지표')).toBeTruthy()
    expect(screen.getByText('208개')).toBeTruthy()
    expect(screen.getByText('2,596,733,728원')).toBeTruthy()
    expect(screen.getByText('12,465,323명·회')).toBeTruthy()
    expect(screen.getByText('37,068명')).toBeTruthy()
    expect(screen.getByText('7,365명')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: '공식 원문 보기' })).toHaveLength(5)
    expect(screen.queryByText('NO_NATIONWIDE_FACTS')).toBeNull()
    expect(screen.queryByText('administrative_dong_mapping')).toBeNull()
    expect(screen.queryByText('estimated_store_sales')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: '가맹 조건' }))
    expect(screen.getByText('확인 완료')).toBeTruthy()
    expect(screen.getByText('본사 확인 필요')).toBeTruthy()
    expect(screen.queryByText('brand-1')).toBeNull()
    expect(screen.queryByText('VERIFIED')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: '필요자금' }))
    expect(screen.getByText('권리금·영업권')).toBeTruthy()
    expect(screen.queryByText('premium')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: '위험과 검증' }))
    expect(screen.getByText('높은 위험 · 2개 항목')).toBeTruthy()
    expect(screen.queryByText('risk-1')).toBeNull()
    expect(screen.queryByText('risk-2')).toBeNull()
  })
})
