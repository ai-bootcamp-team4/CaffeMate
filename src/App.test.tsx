import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AuthGateway, AuthSession } from './auth'
import type { ControlApiClient, HeadFence, Project, ResultView, WorkflowProgress } from './apiClient'

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
    founder: {},
    area: {
      resolution_status: 'RESOLVED',
      display_name: '수원시 영통구 원천동',
      coverage_profile: 'R2_REGIONAL_CONNECTOR',
      evidence_ids: ['evidence-area'],
      unavailable_fields: ['estimated_store_sales'],
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
    financial_summary: {
      initial_cash: { currency: 'KRW', low: 70_000_000, base: 80_000_000, high: 90_000_000, provenance_refs: ['evidence-cost'] },
      monthly_fixed_cost: { currency: 'KRW', low: 4_000_000, base: 5_000_000, high: 6_000_000, provenance_refs: ['evidence-cost'] },
      break_even_monthly_sales_krw: 15_000_000, required_daily_orders: 80, unknown_cost_fields: ['premium'],
    },
    missing_fields: [{ field: 'royalty', impact: '월 고정비가 바뀝니다.', next_check: '본사에 확인합니다.' }],
    risks: [{ risk_id: 'risk-1', severity: 'HIGH', summary: '출점 가능 여부가 확인되지 않았습니다.', evidence_refs: [] }],
    counterfactuals: [{ variable: 'rent', condition: '월세 15% 감소', decision_impact: '검토 우선순위가 상승합니다.' }],
    next_actions: ['본사 출점 가능 여부 확인'],
  }],
}

const workflow = { workflow_run_id: 'workflow-1', project_id: 'project-1', workflow_code: 'FIRST_PROPOSAL' as const, status: 'SUCCEEDED' as const, head, created_at: '2026-08-22T00:01:00Z', updated_at: '2026-08-22T00:02:00Z' }
const progress: WorkflowProgress = { ...workflow, completed_stage_count: 9, total_stage_count: 9, current_stage_codes: [], terminal_reason_codes: [], human_review_requests: [], poll_after_ms: null }

function setup() {
  const session: AuthSession = { uid: 'user-1', displayName: '민석', getIdToken: vi.fn(async () => 'id-token'), signOut: vi.fn(async () => undefined) }
  const authGateway: AuthGateway = { restoreSession: vi.fn(async () => null), signIn: vi.fn(async () => session) }
  const client: ControlApiClient = {
    createProject: vi.fn(async () => ({ ...project, state: null })),
    listProjects: vi.fn(async () => []),
    confirmOnboarding: vi.fn(async () => project),
    startFirstProposal: vi.fn(async () => workflow),
    getWorkflow: vi.fn(async () => progress),
    getResult: vi.fn(async () => result),
    createFeedbackPreview: vi.fn(async () => { throw new Error('not used') }),
    confirmFeedback: vi.fn(async () => { throw new Error('not used') }),
    cancelFeedback: vi.fn(async () => { throw new Error('not used') }),
    selectCandidate: vi.fn(async () => ({ selection_id: 'selection-1', candidate_id: 'candidate-1', required_evidence: [], property_intake_enabled: true, document_intake_enabled: true })),
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
    expect(client.startFirstProposal).toHaveBeenCalledWith('project-1')
    expect(client.getWorkflow).toHaveBeenCalledWith('project-1', 'workflow-1')
    expect(screen.getAllByText('출점 가능 여부 확인이 필요한 조건부 후보입니다.').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/70,000,000원/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/가상 목업값/)).toBeNull()
  })

  it('persists an explicit next-preparation selection through the API', async () => {
    const { client } = setup()
    await completeOnboarding()
    fireEvent.click(screen.getByRole('button', { name: '다음 준비 대상으로 선택' }))
    await waitFor(() => expect(client.selectCandidate).toHaveBeenCalledWith('project-1', result, 'candidate-1'))
    expect(await screen.findByText(/다음 준비 대상으로 선택했습니다/)).toBeTruthy()
  })
})
