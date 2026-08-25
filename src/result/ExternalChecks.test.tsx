import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ResultCandidate } from '../apiClient'
import { ExternalChecks } from './ExternalChecks'

afterEach(cleanup)

function candidate(requirements: NonNullable<ResultCandidate['verification_requirements']>): ResultCandidate {
  return {
    candidate_id: 'candidate-1',
    project_id: 'project-1',
    state_version: 1,
    case_type: 'INDEPENDENT',
    display_name: '개인카페',
    review_status: 'REVIEW_RECOMMENDED',
    reason_codes: [],
    summary: '',
    rank: 1,
    rank_basis: 'ECONOMIC_AND_FOUNDER_FIT',
    is_primary_next_review: false,
    franchise: null,
    independent_model: { model_id: 'model-1', adjusted_fields: [] },
    evidence_refs: [],
    financial_summary: {
      initial_cash: { currency: 'KRW', low: 1, base: 1, high: 1, provenance_refs: [] },
      monthly_fixed_cost: { currency: 'KRW', low: 1, base: 1, high: 1, provenance_refs: [] },
      unknown_cost_fields: [],
    },
    missing_fields: [],
    risks: [],
    counterfactuals: [],
    next_actions: [],
    verification_requirements: requirements,
  }
}

describe('ExternalChecks', () => {
  it('does not render an empty external-confirmation section', () => {
    render(<ExternalChecks candidate={candidate([])} />)
    expect(screen.queryByRole('region', { name: 'CaffeMate 밖에서 확인해야 해요' })).toBeNull()
  })

  it('shows authority and concrete evidence needed for external confirmation', () => {
    render(<ExternalChecks candidate={candidate([{
      requirement_code: 'SITE_FACILITY_COMPLIANCE',
      label: '점포 시설기준 최종 확인',
      resolver: 'LOCAL_AUTHORITY',
      authority: '성동구청 휴게음식점 영업신고 담당 부서',
      current_status: 'EXTERNAL_CONFIRMATION_REQUIRED',
      required_evidence: ['SITE_PLAN_AND_AUTHORITY_CONFIRMATION', 'CURRENT_FACILITY_PHOTOS'],
      reason: '현장 확인이 필요합니다.',
      resolution_action: { type: 'EXTERNAL_CONFIRMATION', target_fields: ['site.facility'] },
    }])} />)

    const region = screen.getByRole('region', { name: 'CaffeMate 밖에서 확인해야 해요' })
    expect(within(region).getByText('성동구청 휴게음식점 영업신고 담당 부서', { exact: false })).toBeTruthy()
    expect(within(region).getByText('점포 도면과 관할기관 확인')).toBeTruthy()
    expect(within(region).getByText('현재 시설 사진·현황')).toBeTruthy()
  })
})