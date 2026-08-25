import { cleanup, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { completeOnboarding, result, setup } from '../testSupport/appHarness'

afterEach(cleanup)

describe('result narrative evidence presentation', () => {
  it('explains finance ranges without contradicting document-backed values', async () => {
    setup({
      ...result,
      candidates: result.candidates.map((candidate) => ({
        ...candidate,
        financial_summary: { ...candidate.financial_summary, unknown_cost_fields: [] },
        decision_inputs: candidate.decision_inputs?.map((input) =>
          input.field === 'initial_cash_krw'
            ? {
                ...input,
                source: {
                  ...input.source!,
                  filename: 'regional-rent-benchmark.pdf',
                  page_index: 1,
                  section_path: '영통구 임대료 표',
                },
              }
            : input,
        ),
      })),
    })
    await completeOnboarding()

    expect(screen.getByRole('heading', { name: '돈이 어떻게 계산됐나요?' })).toBeTruthy()
    expect(screen.getByText('실제 입력, 공식 참고값, 가정을 숫자 바로 옆에서 구분합니다.')).toBeTruthy()
    expect(screen.getByText('한국부동산원 상업용부동산 임대동향조사')).toBeTruthy()
    expect(screen.getByText('지역 참고값')).toBeTruthy()
    expect(screen.getByText('지역 참고값이며 실제 점포의 임대 조건은 아닙니다.')).toBeTruthy()
    expect(screen.getByText('regional-rent-benchmark.pdf · 2페이지 · 영통구 임대료 표')).toBeTruthy()
    expect(screen.getAllByText(/적용: 초기 필요자금/).length).toBeGreaterThan(0)
    expect(screen.getByText('계산값이며 실제 달성 가능 매출 예측이 아닙니다.')).toBeTruthy()
  })
})
