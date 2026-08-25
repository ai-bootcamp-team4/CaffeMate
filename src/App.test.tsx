import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ControlApiError } from './apiClient'
import type { HeadFence, ResultView, WorkflowProgress } from './apiClient'
import { completeOnboarding, enterOnboarding, feedbackPreview, head, progress, project, result, resultExplanation, setup, workflow } from './testSupport/appHarness'

afterEach(cleanup)

function openResultAssistant() {
  fireEvent.click(screen.getByRole('button', { name: 'CaffeMate에게 물어보기' }))
  return screen.getByLabelText('CaffeMate에게 물어보기') as HTMLTextAreaElement
}

describe('CaffeMate Control API integration', () => {
  it('lists saved projects after sign-in and resumes a saved result without creating a project', async () => {
    const { client } = setup()
    vi.mocked(client.listProjects).mockResolvedValueOnce([project])

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))

    expect(await screen.findByRole('heading', { name: '이어서 살펴볼 카페 창업안을 선택하세요.' })).toBeTruthy()
    expect(screen.getByText('수원시 영통구 원천동')).toBeTruthy()
    expect(client.createProject).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '계속하기' }))

    expect(await screen.findByRole('heading', { name: '실제 검증 브랜드' })).toBeTruthy()
    expect(client.getResult).toHaveBeenCalledWith('project-1')
    expect(client.startFirstProposal).not.toHaveBeenCalled()
  })

  it('moves a saved project without a result to visible analysis progress', async () => {
    const { client } = setup()
    vi.mocked(client.listProjects).mockResolvedValueOnce([project])
    vi.mocked(client.getResult)
      .mockRejectedValueOnce(new ControlApiError(404, 'RESULT_NOT_FOUND', '결과 없음'))
      .mockResolvedValueOnce(result)
    let finishWorkflow: ((value: WorkflowProgress) => void) | undefined
    vi.mocked(client.getWorkflow).mockImplementationOnce(() => new Promise((resolve) => { finishWorkflow = resolve }))

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
    await screen.findByRole('heading', { name: '이어서 살펴볼 카페 창업안을 선택하세요.' })
    fireEvent.click(screen.getByRole('button', { name: '계속하기' }))

    expect(await screen.findByRole('heading', { name: '저장된 조건으로 분석을 이어가고 있어요' })).toBeTruthy()
    expect(screen.queryByText('불러오는 중')).toBeNull()
    finishWorkflow?.(progress)
    expect(await screen.findByRole('heading', { name: '실제 검증 브랜드' })).toBeTruthy()
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

  it('preserves the onboarding viewport when moving between input steps', async () => {
    setup()
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    try {
      await enterOnboarding()
      scrollTo.mockClear()
      fireEvent.change(screen.getByLabelText('희망 지역'), { target: { value: '수원 원천동' } })
      fireEvent.click(await screen.findByRole('option', { name: /경기도 수원시 영통구 원천동/ }))
      fireEvent.click(screen.getByRole('button', { name: '다음' }))
      expect(await screen.findByRole('heading', { name: '사용할 수 있는 자금은 얼마인가요?' })).toBeTruthy()
      expect(scrollTo).not.toHaveBeenCalled()
      expect(document.activeElement).not.toBe(screen.getByLabelText('현재 자기자금'))
    } finally {
      scrollTo.mockRestore()
    }
  })

  it('runs FIRST_PROPOSAL and renders only the returned result', async () => {
    const { client } = setup()
    await completeOnboarding()
    expect(client.confirmOnboarding).toHaveBeenCalledOnce()
    expect(client.confirmOnboarding).toHaveBeenCalledWith('project-1', expect.any(Object), 'signed-area-selection')
    expect(client.startFirstProposal).toHaveBeenCalledWith('project-1')
    expect(client.getWorkflow).toHaveBeenCalledWith('project-1', 'workflow-1')
    expect(screen.getByRole('heading', { name: '이번 분석의 결론' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '실제 검증 브랜드' })).toBeTruthy()
    expect(screen.getAllByText(/70,000,000원/).length).toBeGreaterThan(0)
    expect(screen.getByText('지역 참고값')).toBeTruthy()
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

    expect(screen.getByRole('heading', { name: '왜 이 안을 먼저 보나요?' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /소형 포장 중심 개인카페/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /좌석 중심 개인카페/ })).toBeTruthy()
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('keeps the current reading position when switching comparison candidates', async () => {
    const comparisonResult: ResultView = {
      ...result,
      candidates: [
        result.candidates[0],
        { ...result.candidates[0], candidate_id: 'candidate-2', display_name: '소형 포장 중심 개인카페', rank: 2, is_primary_next_review: false },
      ],
    }
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    try {
      setup(comparisonResult)
      await completeOnboarding()
      scrollTo.mockClear()

      fireEvent.click(screen.getByRole('button', { name: /소형 포장 중심 개인카페/ }))

      expect(await screen.findByRole('heading', { name: '소형 포장 중심 개인카페' })).toBeTruthy()
      expect(scrollTo).not.toHaveBeenCalled()
    } finally {
      scrollTo.mockRestore()
    }
  })

  it('renders one linear decision narrative before the result assistant', async () => {
    setup()
    await completeOnboarding()

    expect(screen.getByRole('heading', { name: '이번 분석의 결론' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '왜 이 안을 검토할 수 있나요?' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '돈이 어떻게 계산됐나요?' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '같이 살펴본 상권 정보' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '자료를 넣으면 다시 판단할 수 있어요' })).toBeNull()
    expect(screen.queryByRole('heading', { name: '무엇이 바뀌면 판단도 바뀌나요?' })).toBeNull()
    const externalHeading = screen.getByRole('heading', { name: 'CaffeMate 밖에서 확인해야 해요' })
    const preparationHeading = screen.getByRole('heading', { name: '실제로 진행한다면' })
    const assistantLauncher = screen.getByRole('button', { name: 'CaffeMate에게 물어보기' })
    const sectionNav = screen.getByRole('navigation', { name: '결과 바로가기' })
    expect(within(sectionNav).getByRole('link', { name: '결론' }).getAttribute('href')).toBe('#result-conclusion')
    expect(within(sectionNav).getByRole('link', { name: '판정 이유' }).getAttribute('href')).toBe('#result-decision')
    expect(within(sectionNav).getByRole('link', { name: '비용·계산' }).getAttribute('href')).toBe('#result-finance')
    expect(within(sectionNav).getByRole('link', { name: '참고 상권' }).getAttribute('href')).toBe('#result-market')
    expect(within(sectionNav).getByRole('link', { name: '외부 확인' }).getAttribute('href')).toBe('#result-external')
    expect(within(sectionNav).getByRole('link', { name: '진행 절차' }).getAttribute('href')).toBe('#result-preparation')
    expect(externalHeading.compareDocumentPosition(preparationHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(preparationHeading.compareDocumentPosition(assistantLauncher) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByRole('tab', { name: '필요자금' })).toBeNull()
    expect(screen.queryByRole('tab', { name: '상권 신호' })).toBeNull()
  })

  it('keeps external HQ confirmation out of the decisive CaffeMate judgment', async () => {
    setup()
    await completeOnboarding()

    const decisionSection = screen.getByRole('region', { name: '왜 이 안을 검토할 수 있나요?' })
    expect(within(decisionSection).getAllByText(/자금 조건/).length).toBeGreaterThan(0)
    expect(within(decisionSection).queryByText(/본사/)).toBeNull()

    const externalSection = screen.getByRole('region', { name: 'CaffeMate 밖에서 확인해야 해요' })
    expect(within(externalSection).getByText('이 주소의 출점 가능 여부')).toBeTruthy()
    expect(within(externalSection).getByText(/CaffeMate가 확정할 수 없습니다/)).toBeTruthy()
    expect(screen.getByText('비용·예상매출·성공확률 계산에는 사용하지 않았어요.')).toBeTruthy()
  })

  it('loads startup procedures from the initial result without entering refinement', async () => {
    const { client } = setup()
    await completeOnboarding()

    expect(screen.getByRole('heading', { name: '실제로 진행한다면' })).toBeTruthy()
    expect(client.getPreparationGuide).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '창업 준비 절차 보기' }))

    await waitFor(() => expect(client.selectCandidate).toHaveBeenCalledWith('project-1', result, 'candidate-1'))
    await waitFor(() => expect(client.getPreparationGuide).toHaveBeenCalledWith('project-1', 'selection-1'))
    expect(await screen.findByText('신규 영업자 위생교육 이수')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '실제 조건으로 검증하기' })).toBeNull()
  })

  it('opens target-specific numeric refinement from a finance row', async () => {
    const { client } = setup()
    await completeOnboarding()

    expect(screen.queryByRole('button', { name: '실제 조건으로 검증하기' })).toBeNull()
    expect(screen.queryByRole('button', { name: '검증 계속하기' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '실제 매물로 바꾸기' }))

    await waitFor(() => expect(client.selectCandidate).toHaveBeenCalledWith('project-1', result, 'candidate-1'))
    expect(await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '지역 참고값을 실제 임대 조건으로 교체해요' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '직접 입력' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '파일로 불러오기' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '파일로 불러오기' }))
    expect(screen.getByRole('option', { name: '점포 매물 자료' })).toBeTruthy()
    expect(screen.getByRole('option', { name: '상가 임대차계약서' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: '장비 견적서' })).toBeNull()
    expect(screen.getByLabelText('파일 선택')).toBeTruthy()
    expect(screen.queryByLabelText('검증 순서')).toBeNull()
    expect(screen.queryByRole('heading', { name: '계산으로 끝낼 수 없는 조건을 따로 확인해요' })).toBeNull()
    expect(screen.queryByRole('heading', { name: '경제·계약 검토 뒤에 공식 절차를 확인해요' })).toBeNull()
  })

  it('restores the previous result position after returning from numeric refinement', async () => {
    const { client } = setup()
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    const previousScrollY = Object.getOwnPropertyDescriptor(window, 'scrollY')
    Object.defineProperty(window, 'scrollY', { configurable: true, value: 640 })
    try {
      await completeOnboarding()
      scrollTo.mockClear()

      fireEvent.click(screen.getByRole('button', { name: '실제 매물로 바꾸기' }))
      await waitFor(() => expect(client.selectCandidate).toHaveBeenCalledWith('project-1', result, 'candidate-1'))
      expect(await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })).toBeTruthy()
      expect(scrollTo).toHaveBeenCalledWith({ top: 0 })
      scrollTo.mockClear()

      fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))

      expect(await screen.findByRole('heading', { name: '이번 분석의 결론' })).toBeTruthy()
      await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 640 }))
    } finally {
      scrollTo.mockRestore()
      if (previousScrollY) Object.defineProperty(window, 'scrollY', previousScrollY)
    }
  })

  it('refreshes a stale result in the same project before selecting the candidate', async () => {
    const currentHead: HeadFence = { ...head, state_version: 2, workflow_generation: 2 }
    const staleResult: ResultView = { ...result, freshness: 'STALE', current_head: currentHead, stale_head_dimensions: ['state_version'] }
    const refreshedResult: ResultView = {
      ...result,
      result_bundle_id: 'result-2',
      head: currentHead,
      current_head: currentHead,
      freshness: 'CURRENT',
      candidates: result.candidates.map((candidate) => ({ ...candidate, state_version: 2 })),
    }
    const { client } = setup(staleResult)
    await completeOnboarding()
    vi.mocked(client.getResult).mockResolvedValueOnce(refreshedResult)

    fireEvent.click(screen.getByRole('button', { name: '실제 매물로 바꾸기' }))

    await waitFor(() => expect(client.startFirstProposal).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(client.selectCandidate).toHaveBeenCalledWith('project-1', refreshedResult, 'candidate-1'))
    expect(await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })).toBeTruthy()
  })

  it('loads property terms from a listing document into the same manual property form before applying', async () => {
    const { client } = setup()
    await completeOnboarding()
    fireEvent.click(screen.getByRole('button', { name: '실제 매물로 바꾸기' }))
    await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })
    fireEvent.click(screen.getByRole('button', { name: '파일로 불러오기' }))

    const uploadTicket = {
      document_id: 'property-document', document_revision_id: 'property-revision', revision_number: 1,
      object_path: 'users/user-1/projects/project-1/documents/property-document/revisions/1/listing.pdf',
      upload_url: 'https://storage.example.test/upload', method: 'PUT' as const,
      required_headers: { 'Content-Type': 'application/pdf' }, expires_at: '2026-08-25T06:00:00Z', status: 'UPLOAD_PENDING' as const,
    }
    const revision = {
      document_id: 'property-document', document_revision_id: 'property-revision', project_id: 'project-1', revision_number: 1,
      document_type: 'PROPERTY_LISTING' as const, original_filename: 'listing.pdf', content_type: 'application/pdf', size_bytes: 4,
      sha256: 'd'.repeat(64), status: 'EXTRACTION_READY' as const, failure_codes: [],
      created_at: '2026-08-25T05:00:00Z', updated_at: '2026-08-25T05:01:00Z', completed_at: '2026-08-25T05:01:00Z',
    }
    const extractionForm = {
      form_id: 'property-form-1', project_id: 'project-1', document_id: 'property-document', document_revision_id: 'property-revision',
      expected_state_version: 2, form_status: 'READY_FOR_REVIEW', apply_label: '반영하고 다시 계산',
      form_digest: `sha256:${'e'.repeat(64)}`, applied_state_version: null,
      fields: [
        { field_id: 'address', claim_type: 'ADDRESS', label: '점포 주소', raw_value_text: '서울 마포구 공덕동 1-1', extracted_value: '서울 마포구 공덕동 1-1', current_value: '서울 마포구 공덕동 1-1', unit: null, materiality: 'HIGH', extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '매물 정보' }, warnings: [] },
        { field_id: 'area', claim_type: 'AREA', label: '면적', raw_value_text: '35㎡', extracted_value: 35, current_value: 35, unit: '㎡', materiality: 'HIGH', extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '매물 정보' }, warnings: [] },
        { field_id: 'floor', claim_type: 'FLOOR', label: '층', raw_value_text: '1층', extracted_value: '1층', current_value: '1층', unit: null, materiality: 'MEDIUM', extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '매물 정보' }, warnings: [] },
        { field_id: 'deposit', claim_type: 'LEASE_DEPOSIT', label: '보증금', raw_value_text: '4,000만원', extracted_value: 40_000_000, current_value: 40_000_000, unit: '원', materiality: 'HIGH', extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '임대 조건' }, warnings: [] },
        { field_id: 'rent', claim_type: 'MONTHLY_RENT', label: '월세', raw_value_text: '210만원', extracted_value: 2_100_000, current_value: 2_100_000, unit: '원', materiality: 'HIGH', extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '임대 조건' }, warnings: [] },
        { field_id: 'management', claim_type: 'MANAGEMENT_FEE', label: '관리비', raw_value_text: '15만원', extracted_value: 150_000, current_value: 150_000, unit: '원', materiality: 'MEDIUM', extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '임대 조건' }, warnings: [] },
        { field_id: 'key-money', claim_type: 'KEY_MONEY', label: '권리금', raw_value_text: '700만원', extracted_value: 7_000_000, current_value: 7_000_000, unit: '원', materiality: 'MEDIUM', extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '임대 조건' }, warnings: [] },
      ],
    }
    vi.mocked(client.beginDocumentUpload).mockResolvedValueOnce(uploadTicket)
    vi.mocked(client.uploadDocument).mockResolvedValueOnce(undefined)
    vi.mocked(client.completeDocumentUpload).mockResolvedValueOnce(revision)
    vi.mocked(client.getDocumentRevision).mockResolvedValueOnce(revision)
    vi.mocked(client.getDocumentExtractionForm).mockResolvedValueOnce(extractionForm)

    const file = new File(['test'], 'listing.pdf', { type: 'application/pdf' })
    Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('test').buffer })
    fireEvent.change(screen.getByLabelText('파일 선택'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: '업로드하고 값 찾기' }))

    expect(await screen.findByDisplayValue('40000000')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '이 값으로 점포 입력 채우기' }))

    expect((await screen.findByLabelText('점포 주소') as HTMLInputElement).value).toBe('서울 마포구 공덕동 1-1')
    expect((screen.getByLabelText('면적(㎡)') as HTMLInputElement).value).toBe('35')
    expect((screen.getByLabelText('보증금(만원)') as HTMLInputElement).value).toBe('4000')
    expect((screen.getByLabelText('월세(만원)') as HTMLInputElement).value).toBe('210')
    expect((screen.getByLabelText('관리비(만원)') as HTMLInputElement).value).toBe('15')
    expect((screen.getByLabelText('권리금(만원)') as HTMLInputElement).value).toBe('700')
    expect(client.applyDocumentExtractionForm).not.toHaveBeenCalled()
    expect(client.applyPropertyTerms).not.toHaveBeenCalled()
  })

  it('recalculates the selected candidate and explains what changed', async () => {
    const previousInput = result.candidates[0].decision_inputs?.find((input) => input.field === 'initial_cash_krw') ?? null
    const actualRentInput = {
      field: 'monthly_rent_krw', label: '실제 월세', value: 2_000_000,
      provenance: 'USER_INPUT' as const, resolution_status: 'USER_CONFIRMED_FACT' as const,
      decision_role: 'FINANCE_INPUT' as const, source: null, applied_to: ['MONTHLY_FIXED_COST'],
      replaceable_by: [], limitation_code: null, resolution_action: { type: 'PROPERTY_TERMS' as const, target_fields: ['monthly_rent_krw'] },
    }
    const recalculated: ResultView = {
      ...result,
      result_bundle_id: 'result-2',
      candidates: [{
        ...result.candidates[0],
        candidate_id: 'candidate-recalculated',
        decision_inputs: [...(result.candidates[0].decision_inputs ?? []), actualRentInput],
        financial_summary: {
          ...result.candidates[0].financial_summary,
          initial_cash: { currency: 'KRW', low: 60_000_000, base: 70_000_000, high: 80_000_000, provenance_refs: ['property-1'] },
          monthly_fixed_cost: { currency: 'KRW', low: 2_400_000, base: 2_400_000, high: 2_400_000, provenance_refs: ['property-1'] },
        },
      }],
      decision_delta: {
        previous_result_bundle_id: 'result-1', current_result_bundle_id: 'result-2', primary_candidate_changed: false,
        requires_human_review: false, human_review_reason_codes: [],
        candidate_changes: [{
          candidate_key: 'FRANCHISE:brand-1', display_name: '실제 검증 브랜드', change_type: 'UPDATED',
          previous_rank: 1, current_rank: 1, previous_review_status: 'REVIEW_RECOMMENDED', current_review_status: 'REVIEW_RECOMMENDED',
          initial_cash_base_delta_krw: -10_000_000, monthly_fixed_cost_base_delta_krw: -2_600_000, break_even_monthly_sales_delta_krw: null,
          input_changes: [{ field: 'monthly_rent_krw', before: previousInput, after: actualRentInput, applied_to: ['MONTHLY_FIXED_COST'] }],
          gate_changes: [{ gate_type: 'CAPITAL', previous_status: 'PASS', current_status: 'PASS', reason_code: 'CURRENT_CONSTRAINTS_SATISFIED' }],
        }],
      },
    }
    const { client } = setup()
    await completeOnboarding()
    fireEvent.click(screen.getByRole('button', { name: '실제 매물로 바꾸기' }))
    await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })
    vi.mocked(client.getResult).mockResolvedValueOnce(recalculated)

    fireEvent.change(screen.getByLabelText('점포 주소'), { target: { value: '서울 마포구 공덕동 1-1' } })
    fireEvent.change(screen.getByLabelText('면적(㎡)'), { target: { value: '33' } })
    fireEvent.change(screen.getByLabelText('보증금(만원)'), { target: { value: '3000' } })
    fireEvent.change(screen.getByLabelText('월세(만원)'), { target: { value: '200' } })
    fireEvent.change(screen.getByLabelText('관리비(만원)'), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText('권리금(만원)'), { target: { value: '1000' } })
    fireEvent.change(screen.getByLabelText('층'), { target: { value: '2층' } })
    fireEvent.click(screen.getByRole('button', { name: '이 조건으로 다시 판단' }))

    await waitFor(() => expect(client.applyPropertyTerms).toHaveBeenCalledWith('project-1', 'selection-1', 2, expect.objectContaining({ monthly_rent_krw: 2_000_000, deposit_krw: 30_000_000, floor: '2층' })))
    expect(await screen.findByRole('heading', { name: '입력값을 바꾼 뒤 무엇이 달라졌나요?' })).toBeTruthy()
    expect(screen.getAllByText('실제 월세').length).toBeGreaterThan(0)
    expect(screen.getByText(/지역 참고값.*실제 입력으로 확인/)).toBeTruthy()
    expect(screen.getByText('자금 조건: 유지 · 자기자금으로 충당 가능')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('월세(만원)'), { target: { value: '190' } })
    fireEvent.click(screen.getByRole('button', { name: '이 조건으로 다시 판단' }))
    await waitFor(() => expect(client.applyPropertyTerms).toHaveBeenLastCalledWith('project-1', 'selection-1', 3, expect.objectContaining({ monthly_rent_krw: 1_900_000 })))
  })

  it('uploads a document, exposes editable extracted values, applies them, and refreshes the result', async () => {
    const { client } = setup()
    await completeOnboarding()
    fireEvent.click(screen.getByRole('button', { name: '장비 견적 반영하기' }))
    await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })

    const uploadTicket = {
      document_id: 'document-1', document_revision_id: 'revision-1', revision_number: 1,
      object_path: 'users/user-1/projects/project-1/documents/document-1/revisions/1/file.pdf',
      upload_url: 'https://storage.example.test/upload', method: 'PUT' as const,
      required_headers: { 'Content-Type': 'application/pdf' }, expires_at: '2026-08-23T01:00:00Z', status: 'UPLOAD_PENDING' as const,
    }
    const revision = {
      document_id: 'document-1', document_revision_id: 'revision-1', project_id: 'project-1', revision_number: 1,
      document_type: 'EQUIPMENT_QUOTE' as const, original_filename: 'lease.pdf', content_type: 'application/pdf', size_bytes: 4,
      sha256: 'a'.repeat(64), status: 'EXTRACTION_READY' as const, failure_codes: [],
      created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:01:00Z', completed_at: '2026-08-23T00:01:00Z',
    }
    const extractionForm = {
      form_id: 'form-1', project_id: 'project-1', document_id: 'document-1', document_revision_id: 'revision-1',
      expected_state_version: 2, form_status: 'READY_FOR_REVIEW', apply_label: '반영하고 다시 계산',
      form_digest: `sha256:${'b'.repeat(64)}`, applied_state_version: null,
      fields: [{
        field_id: 'monthly_rent_krw', claim_type: 'MONTHLY_RENT', label: '월세', raw_value_text: '220만원',
        extracted_value: 2_200_000, current_value: 2_200_000, unit: '원', materiality: 'HIGH', extraction_status: 'AUTO_FILLED' as const,
        edit_status: 'UNCHANGED' as const, anchor: { page_index: 0, section_path: '임대 조건' }, warnings: [],
      }],
    }
    const appliedForm = { ...extractionForm, form_digest: `sha256:${'c'.repeat(64)}`, fields: [{ ...extractionForm.fields[0], current_value: 2_000_000, edit_status: 'EDITED' as const }] }
    vi.mocked(client.beginDocumentUpload).mockResolvedValueOnce(uploadTicket)
    vi.mocked(client.uploadDocument).mockResolvedValueOnce(undefined)
    vi.mocked(client.completeDocumentUpload).mockResolvedValueOnce(revision)
    vi.mocked(client.getDocumentRevision).mockResolvedValueOnce(revision)
    vi.mocked(client.getDocumentExtractionForm).mockResolvedValueOnce(extractionForm)
    vi.mocked(client.updateDocumentExtractionForm).mockResolvedValueOnce(appliedForm)
    vi.mocked(client.applyDocumentExtractionForm).mockResolvedValueOnce({
      application_id: 'application-1', project_id: 'project-1', document_revision_id: 'revision-1', applied_state_version: 3,
      recompute_workflow_run_id: 'workflow-1', claims: [], conflicts: [], requires_human_review: false,
    })
    const recomputedHead = { ...head, state_version: 3, workflow_generation: 2 }
    const excludedResult: ResultView = {
      ...result,
      result_bundle_id: 'result-after-document',
      workflow_run_id: 'workflow-document-recompute',
      head: recomputedHead,
      current_head: recomputedHead,
      primary_candidate_id: null,
      outcome_status: 'NO_REVIEWABLE_CANDIDATES',
      candidates: [{
        ...result.candidates[0],
        candidate_id: 'candidate-after-document',
        state_version: 3,
        review_status: 'EXCLUDED',
        reason_codes: ['INITIAL_CAPITAL_EXCEEDS_AVAILABLE_FUNDS'],
        rank: null,
        rank_basis: 'NOT_RANKED',
        is_primary_next_review: false,
      }],
    }
    vi.mocked(client.getResult).mockResolvedValueOnce(excludedResult)

    const file = new File(['test'], 'equipment.pdf', { type: 'application/pdf' })
    Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('test').buffer })
    fireEvent.change(screen.getByLabelText('파일 선택'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: '업로드하고 값 찾기' }))

    expect(await screen.findByDisplayValue('2200000')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('월세 (원)'), { target: { value: '2000000' } })
    fireEvent.click(screen.getByRole('button', { name: '반영하고 다시 계산' }))

    await waitFor(() => expect(client.updateDocumentExtractionForm).toHaveBeenCalledWith('project-1', extractionForm, [{ field_id: 'monthly_rent_krw', value: 2_000_000 }]))
    await waitFor(() => expect(client.applyDocumentExtractionForm).toHaveBeenCalledWith('project-1', appliedForm))
    expect(await screen.findByText('문서 값을 반영하고 창업안을 다시 계산했어요.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))
    expect(await screen.findByRole('heading', { name: '이번 분석의 결론' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '왜 이 안은 지금 진행하기 어려운가요?' })).toBeTruthy()
    expect(screen.queryByText('입력 조건이 바뀌었어요.')).toBeNull()
  })

  it('keeps the initial result usable when official procedure lookup fails', async () => {
    const { client } = setup()
    vi.mocked(client.getPreparationGuide).mockRejectedValueOnce(new Error('temporary procedure lookup failure'))
    await completeOnboarding()

    fireEvent.click(screen.getByRole('button', { name: '창업 준비 절차 보기' }))
    expect(await screen.findByRole('button', { name: '다시 확인' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '실제 매물로 바꾸기' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'CaffeMate에게 물어보기' })).toBeTruthy()
  })

  it('shows an authoritative funding failure without a separate condition-change CTA', async () => {
    const failedCandidate = {
      ...result.candidates[0],
      review_status: 'EXCLUDED' as const,
      reason_codes: ['MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS'], rank: null, rank_basis: 'NOT_RANKED', is_primary_next_review: false,
      decision_trace: { gates: [{ gate_type: 'CAPITAL', status: 'FAIL' as const, reason_code: 'MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS', decisive_input_refs: ['own_funds_krw', 'initial_cash_krw'], metrics: { own_funds_krw: 50_000_000, minimum_required_krw: 70_000_000, shortfall_krw: 20_000_000 } }] },
    }
    setup({ ...result, primary_candidate_id: null, outcome_status: 'NO_REVIEWABLE_CANDIDATES', candidates: [failedCandidate] })
    await completeOnboarding()

    expect(screen.getByText('자금 조건이 현재 판단을 막고 있어요.')).toBeTruthy()
    expect(screen.getByText('최소 부족액')).toBeTruthy()
    expect(screen.getByText('20,000,000원')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '내 조건을 바꾸고 다시 비교하기' })).toBeNull()
    expect(screen.getByRole('button', { name: 'CaffeMate에게 물어보기' })).toBeTruthy()
  })

  it('keeps the persistent result assistant compact until the user opens it', async () => {
    setup()

    expect(screen.queryByRole('button', { name: 'CaffeMate에게 물어보기' })).toBeNull()
    await completeOnboarding()

    const assistant = screen.getByTestId('result-assistant-dock')
    expect(assistant.classList.contains('result-assistant-dock')).toBe(true)
    const launcher = screen.getByRole('button', { name: 'CaffeMate에게 물어보기' })
    expect(launcher.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('textbox', { name: 'CaffeMate에게 물어보기' })).toBeNull()
    expect(screen.queryByRole('button', { name: '왜 이 안을 먼저 보나요?' })).toBeNull()

    fireEvent.click(launcher)

    const input = screen.getByRole('textbox', { name: 'CaffeMate에게 물어보기' }) as HTMLTextAreaElement
    expect(input).toBeTruthy()
    expect(screen.getByRole('button', { name: '왜 이 안을 먼저 보나요?' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '예산을 1억으로 바꿔줘' })).toBeTruthy()
    fireEvent.change(input, { target: { value: '접어도 남아야 하는 초안' } })
    const collapse = screen.getByRole('button', { name: 'CaffeMate 채팅 접기' })
    expect(collapse.getAttribute('aria-expanded')).toBe('true')

    fireEvent.click(collapse)

    expect(screen.getByRole('button', { name: 'CaffeMate에게 물어보기' })).toBeTruthy()
    expect(screen.queryByRole('textbox', { name: 'CaffeMate에게 물어보기' })).toBeNull()
    openResultAssistant()
    expect((screen.getByRole('textbox', { name: 'CaffeMate에게 물어보기' }) as HTMLTextAreaElement).value).toBe('접어도 남아야 하는 초안')
    expect(screen.queryByRole('button', { name: '조건 바꾸기' })).toBeNull()
    expect(screen.queryByRole('heading', { name: '조건 변경 제안' })).toBeNull()
  })

  it('answers a result question with evidence without changing state', async () => {
    const { client } = setup()
    await completeOnboarding()
    openResultAssistant()

    fireEvent.click(screen.getByRole('button', { name: '왜 이 안을 먼저 보나요?' }))

    expect(await screen.findByText(resultExplanation.conclusion)).toBeTruthy()
    expect(screen.getByText('서울시 상권분석서비스')).toBeTruthy()
    expect(screen.getByRole('link', { name: '서울시 상권분석서비스 근거 원문 보기' })).toBeTruthy()
    expect(screen.getByText('답변을 확인했어요. 현재 결과는 바뀌지 않았습니다.')).toBeTruthy()
    expect(client.explainResult).toHaveBeenCalledWith('project-1', result, '왜 이 안을 먼저 보나요?', 'candidate-1')
    expect(client.createFeedbackPreview).not.toHaveBeenCalled()
  })

  it('keeps answer auto-scroll inside the assistant instead of moving the result page', async () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    try {
      setup()
      await completeOnboarding()
      openResultAssistant()

      fireEvent.click(screen.getByRole('button', { name: '왜 이 안을 먼저 보나요?' }))

      expect(await screen.findByText(resultExplanation.conclusion)).toBeTruthy()
      expect(scrollIntoView).not.toHaveBeenCalled()
    } finally {
      delete (Element.prototype as Partial<Element>).scrollIntoView
    }
  })

  it('keeps internal codes and identifiers out of the explanation UI', async () => {
    const { client } = setup()
    vi.mocked(client.explainResult).mockResolvedValueOnce({
      ...resultExplanation,
      conclusion: 'HQ_CONFIRMATION_REQUIRED 상태이며 risk-a1b2c3를 확인해야 합니다.',
      reasons: ['candidate-123의 MATERIAL_COST_UNKNOWN 항목을 확인합니다.'],
      evidence: [{
        ...resultExplanation.evidence[0],
        label: 'evidence.internal_code',
        value: 'proposal-123',
        source_title: 'brand-secret-id',
      }],
      unknowns: ['FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED'],
      decision_change_conditions: ['assumption.hidden_value가 바뀌는 경우'],
    })
    await completeOnboarding()
    openResultAssistant()

    fireEvent.click(screen.getByRole('button', { name: '왜 이 안을 먼저 보나요?' }))

    await screen.findByText(/본사 확인 필요 상태이며/)
    const visibleText = document.body.textContent ?? ''
    expect(visibleText).not.toContain('HQ_CONFIRMATION_REQUIRED')
    expect(visibleText).not.toContain('risk-a1b2c3')
    expect(visibleText).not.toContain('candidate-123')
    expect(visibleText).not.toContain('MATERIAL_COST_UNKNOWN')
    expect(visibleText).not.toContain('evidence.internal_code')
    expect(visibleText).not.toContain('proposal-123')
    expect(visibleText).not.toContain('brand-secret-id')
    expect(visibleText).not.toContain('FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED')
    expect(visibleText).not.toContain('assumption.hidden_value')
  })

  it.each([
    [
      409,
      'RESULT_EXPLANATION_PRECONDITION_FAILED',
      '결과가 갱신되었거나 아직 설명할 준비가 끝나지 않았어요. 최신 결과를 다시 확인한 뒤 질문해 주세요.',
    ],
    [
      503,
      'RESULT_EXPLANATION_UNAVAILABLE',
      '결과 설명 기능에 잠시 연결할 수 없어요. 현재 결과는 그대로 보관되어 있으니 잠시 후 다시 질문해 주세요.',
    ],
  ])('shows a friendly recovery action when explanation fails with %i', async (status, code, expectedMessage) => {
    const { client } = setup()
    vi.mocked(client.explainResult).mockRejectedValueOnce(new ControlApiError(status, code, code))
    await completeOnboarding()
    openResultAssistant()

    fireEvent.click(screen.getByRole('button', { name: '왜 이 안을 먼저 보나요?' }))

    expect(await screen.findByText(expectedMessage)).toBeTruthy()
    expect(document.body.textContent).not.toContain(code)
  })

  it('routes a condition-change utterance from the same chat into preview before applying it', async () => {
    const { client } = setup()
    vi.mocked(client.explainResult).mockResolvedValueOnce({ ...resultExplanation, suggested_action: 'OPEN_CONDITION_CHANGE' })
    vi.mocked(client.createFeedbackPreview).mockResolvedValueOnce(feedbackPreview)
    vi.mocked(client.confirmFeedback).mockResolvedValueOnce({
      preview: { ...feedbackPreview, status: 'CONFIRMED' },
      state_version: 2,
      workflow: null,
    })
    await completeOnboarding()
    openResultAssistant()

    fireEvent.change(screen.getByLabelText('CaffeMate에게 물어보기'), {
      target: { value: feedbackPreview.latest_user_input },
    })
    fireEvent.click(screen.getByRole('button', { name: '보내기' }))

    expect(await screen.findByRole('heading', { name: '적용 전 변경 확인' })).toBeTruthy()
    expect(screen.getByText('조건 변경안을 만들었습니다. 적용 전 내용을 확인해 주세요.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'CaffeMate 채팅 접기' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('CaffeMate에게 물어보기') as HTMLTextAreaElement).disabled).toBe(true)
    expect(client.explainResult).toHaveBeenCalledWith('project-1', result, feedbackPreview.latest_user_input, 'candidate-1')
    expect(client.createFeedbackPreview).toHaveBeenCalledWith('project-1', feedbackPreview.latest_user_input)

    fireEvent.click(screen.getByRole('button', { name: '변경 적용' }))

    await waitFor(() => expect(client.confirmFeedback).toHaveBeenCalledWith('project-1', feedbackPreview))
    expect(await screen.findByText('확인한 변경안을 반영하고 결과를 갱신했습니다.')).toBeTruthy()
  })

  it('exposes empty-input and unified-chat loading states accessibly', async () => {
    const { client } = setup()
    await completeOnboarding()
    openResultAssistant()

    fireEvent.click(screen.getByRole('button', { name: '보내기' }))
    const input = screen.getByLabelText('CaffeMate에게 물어보기') as HTMLTextAreaElement
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByText('궁금한 점이나 바꾸고 싶은 조건을 입력해 주세요.')).toBeTruthy()

    let resolveExplanation: ((value: typeof resultExplanation) => void) | undefined
    vi.mocked(client.explainResult).mockImplementationOnce(() => new Promise((resolve) => { resolveExplanation = resolve }))
    fireEvent.change(input, { target: { value: '왜 이 안을 먼저 보나요?' } })
    expect(input.getAttribute('aria-invalid')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '보내기' }))

    const loadingButton = screen.getByRole('button', { name: '확인 중' })
    expect(loadingButton.getAttribute('aria-busy')).toBe('true')
    expect((loadingButton as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'CaffeMate 채팅 접기' }) as HTMLButtonElement).disabled).toBe(true)
    resolveExplanation?.(resultExplanation)
    expect(await screen.findByText(resultExplanation.conclusion)).toBeTruthy()
  })

  it('returns clarification requests to an editable condition input', async () => {
    const clarification = {
      ...feedbackPreview,
      status: 'CLARIFICATION_REQUIRED' as const,
      after_founder: null,
      clarifying_questions: ['어떤 브랜드를 제외할지 알려 주세요.'],
      proposal_digest: null,
    }
    const { client } = setup()
    vi.mocked(client.explainResult).mockResolvedValueOnce({ ...resultExplanation, suggested_action: 'OPEN_CONDITION_CHANGE' })
    vi.mocked(client.createFeedbackPreview).mockResolvedValueOnce(clarification)
    vi.mocked(client.cancelFeedback).mockResolvedValueOnce({
      preview: { ...clarification, status: 'CANCELLED' },
      state_version: null,
      workflow: null,
    })
    await completeOnboarding()
    openResultAssistant()

    fireEvent.change(screen.getByLabelText('CaffeMate에게 물어보기'), {
      target: { value: '브랜드를 빼고 싶어요.' },
    })
    fireEvent.click(screen.getByRole('button', { name: '보내기' }))

    expect(await screen.findByText('어떤 브랜드를 제외할지 알려 주세요.')).toBeTruthy()
    expect((screen.getByRole('button', { name: '변경 적용' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '입력 다시 하기' }))

    await waitFor(() => expect((screen.getByLabelText('CaffeMate에게 물어보기') as HTMLTextAreaElement).disabled).toBe(false))
    expect(screen.getByText('입력을 수정할 수 있습니다. 현재 결과는 바뀌지 않았습니다.')).toBeTruthy()
  })

  it('keeps internal result codes and identifiers out of the linear narrative', async () => {
    setup()
    await completeOnboarding()

    const visibleText = document.body.textContent ?? ''
    for (const internal of ['HQ_CONFIRMATION_REQUIRED', 'evidence-franchise', 'NO_NATIONWIDE_FACTS', 'administrative_dong_mapping', 'estimated_store_sales', 'RESOLVED_BENCHMARK', 'DOCUMENT_REQUIRED', 'risk-1', 'risk-2']) {
      expect(visibleText).not.toContain(internal)
    }
    expect(screen.getByRole('heading', { name: '같이 살펴본 상권 정보' })).toBeTruthy()
    expect(screen.getByText('208개')).toBeTruthy()
    expect(screen.getByText('2,596,733,728원')).toBeTruthy()
    expect(screen.getByText('12,465,323명·회')).toBeTruthy()
    expect(screen.getByText('37,068명')).toBeTruthy()
    expect(screen.getByText('7,365명')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: '공식 원문 보기' })).toHaveLength(5)
    expect(screen.getByText('지역 참고값')).toBeTruthy()
    expect(screen.getByText('이 주소의 출점 가능 여부')).toBeTruthy()
    expect(screen.queryByText('커피전문점 영업신고 및 사업자등록')).toBeNull()
  })

})
