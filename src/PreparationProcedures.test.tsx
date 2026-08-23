import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PreparationGuide } from './apiClient'
import { PreparationProcedures } from './PreparationProcedures'

afterEach(cleanup)

function guide(): PreparationGuide {
  return {
    project_id: 'project-1',
    selection_id: 'selection-1',
    candidate_id: 'candidate-1',
    candidate_type: 'INDEPENDENT',
    jurisdiction_code: '1168010300',
    jurisdiction_display_name: '서울특별시 강남구 개포동',
    as_of: '2026-08-23',
    status: 'REVIEW_REQUIRED',
    procedures: [
      {
        procedure_type: 'BUSINESS_REGISTRATION',
        status: 'OK',
        steps: [
          {
            procedure_type: 'BUSINESS_REGISTRATION',
            step_order: 2,
            title: '<p>사업자등록 신청</p> Read Frog 자세히 보기',
            required: true,
            authority: '국세청',
            source_date: '2026-08-20',
            evidence_id: 'evidence-2',
          },
          {
            procedure_type: 'BUSINESS_REGISTRATION',
            step_order: 1,
            title: '사업자등록 신청',
            required: true,
            authority: '국세청',
            source_date: '2026-08-20',
            evidence_id: 'evidence-1',
          },
        ],
        missing_fields: [],
        conflicts: [],
        error_codes: [],
        source_trace: [
          { source_ref: 'https://www.nts.go.kr/official' },
          { source_ref: 'https://www.nts.go.kr/official' },
        ],
      },
    ],
    source_trace: [{ source_ref: 'https://www.easylaw.go.kr/guide' }],
    evidence_records: [],
    human_actions_only: true,
    external_submission_performed: false,
    generated_at: '2026-08-23T21:30:00+09:00',
  } as unknown as PreparationGuide
}

describe('PreparationProcedures', () => {
  it('turns grounded procedure data into concise actionable sections', () => {
    render(<PreparationProcedures guide={guide()} busy={false} error="" onRetry={() => undefined} />)

    expect(screen.getByRole('heading', { name: '사업자등록' })).toBeTruthy()
    expect(screen.getByText('해야 할 일')).toBeTruthy()
    expect(screen.getByText('준비물')).toBeTruthy()
    expect(screen.getByText('신청처')).toBeTruthy()
    expect(screen.getByText('주의사항')).toBeTruthy()
    expect(screen.getByText('공식 출처')).toBeTruthy()
    expect(screen.getByText('사업자등록 신청')).toBeTruthy()
    expect(screen.getAllByText('사업자등록 신청')).toHaveLength(1)
    expect(screen.queryByText(/Read Frog/)).toBeNull()
    expect(screen.queryByText(/<p>/)).toBeNull()
    expect(screen.getByText('국세청')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: '공식 원문 보기' })).toHaveLength(1)
    expect(screen.getByRole<HTMLAnchorElement>('link', { name: '공식 원문 보기' }).href).toBe('https://www.nts.go.kr/official')
  })

  it('shows a retry action only when loading failed without usable procedures', () => {
    const retry = vi.fn()
    render(<PreparationProcedures guide={null} busy={false} error="연결 실패" onRetry={retry} />)

    fireEvent.click(screen.getByRole('button', { name: '다시 확인' }))
    expect(retry).toHaveBeenCalledOnce()
    expect(screen.getByText('공식 절차 자료를 아직 연결하지 못했어요')).toBeTruthy()
  })

  it('announces loading without rendering an empty-state retry', () => {
    render(<PreparationProcedures guide={null} busy error="연결 실패" onRetry={() => undefined} />)

    expect(screen.getByRole('status').textContent).toContain('공식 절차를 확인하고 있어요.')
    expect(screen.queryByRole('button', { name: '다시 확인' })).toBeNull()
  })
})
