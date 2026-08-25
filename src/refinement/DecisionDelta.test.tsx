import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { result } from '../testSupport/appHarness'
import { DecisionDelta } from './DecisionDelta'

afterEach(cleanup)

describe('DecisionDelta', () => {
  it('describes a borrowing-driven gate change without exposing conditional-review jargon', () => {
    const baseCandidate = result.candidates[0]
    const candidate = { ...baseCandidate, review_status: 'CONDITIONAL_REVIEW' as const }
    const delta = {
      previous_result_bundle_id: 'before',
      current_result_bundle_id: 'after',
      primary_candidate_changed: false,
      requires_human_review: false,
      human_review_reason_codes: [],
      candidate_changes: [{
        candidate_key: 'FRANCHISE:brand-1',
        display_name: candidate.display_name,
        change_type: 'UPDATED',
        previous_rank: null,
        current_rank: 1,
        previous_review_status: 'EXCLUDED' as const,
        current_review_status: 'CONDITIONAL_REVIEW' as const,
        initial_cash_base_delta_krw: 0,
        monthly_fixed_cost_base_delta_krw: 0,
        break_even_monthly_sales_delta_krw: 0,
        input_changes: [],
        gate_changes: [{
          gate_type: 'CAPITAL',
          previous_status: 'FAIL',
          current_status: 'CONDITIONAL',
          reason_code: 'CAPITAL_COVERAGE_REQUIRES_CONFIRMATION',
        }],
      }],
    }

    render(<DecisionDelta delta={delta} candidate={candidate} previousFinancialSummary={baseCandidate.financial_summary} />)

    expect(screen.getByText('후보 검토 상태')).toBeTruthy()
    expect(screen.getAllByText('자기자금만으로 부족').length).toBeGreaterThan(0)
    expect(screen.getAllByText('대출 고려 시 검토 가능').length).toBeGreaterThan(0)
    expect(screen.queryByText('조건부 검토')).toBeNull()
    expect(screen.getByText('자금 조건: 자기자금만으로 부족 → 대출 고려 시 검토 가능')).toBeTruthy()
  })

  it('does not render a meaningless arrow when document or manual input keeps the same status', () => {
    const baseCandidate = result.candidates[0]
    const candidate = { ...baseCandidate, review_status: 'CONDITIONAL_REVIEW' as const }
    const delta = {
      previous_result_bundle_id: 'before',
      current_result_bundle_id: 'after',
      primary_candidate_changed: false,
      requires_human_review: false,
      human_review_reason_codes: [],
      candidate_changes: [{
        candidate_key: 'FRANCHISE:brand-1',
        display_name: candidate.display_name,
        change_type: 'UPDATED',
        previous_rank: 1,
        current_rank: 1,
        previous_review_status: 'CONDITIONAL_REVIEW' as const,
        current_review_status: 'CONDITIONAL_REVIEW' as const,
        initial_cash_base_delta_krw: 5_000_000,
        monthly_fixed_cost_base_delta_krw: 0,
        break_even_monthly_sales_delta_krw: 0,
        input_changes: [],
        gate_changes: [{
          gate_type: 'CAPITAL',
          previous_status: 'CONDITIONAL',
          current_status: 'CONDITIONAL',
          reason_code: 'CAPITAL_COVERAGE_REQUIRES_CONFIRMATION',
        }],
      }],
    }

    render(<DecisionDelta delta={delta} candidate={candidate} previousFinancialSummary={baseCandidate.financial_summary} />)

    expect(screen.getByText('검토 상태 유지')).toBeTruthy()
    expect(screen.getByText('실제 비용 범위 확인 필요')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '입력값을 바꾼 뒤 무엇이 달라졌나요?' })).toBeTruthy()
    expect(screen.queryByText('조건부 검토')).toBeNull()
    expect(screen.queryByText(/조건부 검토.*→.*조건부 검토/)).toBeNull()
    expect(screen.getByText('자금 조건: 유지 · 실제 비용 범위 확인 필요')).toBeTruthy()
  })

  it('does not describe a different conditional gate as a capital outcome', () => {
    const baseCandidate = result.candidates[0]
    const candidate = { ...baseCandidate, review_status: 'CONDITIONAL_REVIEW' as const }
    const delta = {
      previous_result_bundle_id: 'before',
      current_result_bundle_id: 'after',
      primary_candidate_changed: false,
      requires_human_review: false,
      human_review_reason_codes: [],
      candidate_changes: [{
        candidate_key: 'FRANCHISE:brand-1',
        display_name: candidate.display_name,
        change_type: 'UPDATED',
        previous_rank: 1,
        current_rank: 1,
        previous_review_status: 'REVIEW_RECOMMENDED' as const,
        current_review_status: 'CONDITIONAL_REVIEW' as const,
        initial_cash_base_delta_krw: 0,
        monthly_fixed_cost_base_delta_krw: 0,
        break_even_monthly_sales_delta_krw: 0,
        input_changes: [],
        gate_changes: [
          { gate_type: 'CAPITAL', previous_status: 'PASS', current_status: 'PASS', reason_code: 'CURRENT_CONSTRAINTS_SATISFIED' },
          { gate_type: 'FOUNDER_FIT', previous_status: 'PASS', current_status: 'CONDITIONAL', reason_code: 'FOUNDER_FIT_REQUIRES_CONFIRMATION' },
        ],
      }],
    }

    render(<DecisionDelta delta={delta} candidate={candidate} previousFinancialSummary={baseCandidate.financial_summary} />)

    expect(screen.getByText('추가 확인 후 판단')).toBeTruthy()
    expect(screen.queryByText('실제 비용 범위 확인 필요')).toBeNull()
  })
})
