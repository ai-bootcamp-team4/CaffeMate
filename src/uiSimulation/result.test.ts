import { describe, expect, it } from 'vitest'
import { result as baseResult, project as baseProject } from '../testSupport/appHarness'
import type { OnboardingValues } from '../onboardingState'
import { buildSimulationProject, buildSimulationResult } from './result'
import { searchSimulationAreas } from './scenarios'

const values: OnboardingValues = {
  targetAreaInput: '수원 원천동',
  ownFundsKrw: '80000000',
  borrowingIntent: 'UNDECIDED',
  cafeTypePreference: 'OPEN_TO_BOTH',
  operationMode: 'DIRECT_FULL_TIME',
  desiredOpeningPeriod: '',
  priorCafeExperience: '',
}

describe('UI simulation result projection', () => {
  it('projects the selected area and founder inputs into the mock project', () => {
    const area = searchSimulationAreas('수원 원천동')[0]
    const projected = buildSimulationProject(baseProject, area, values)
    expect(projected.state?.area.display_name).toBe('경기도 수원시 영통구 원천동')
    expect(projected.state?.founder.own_funds_krw).toBe(80_000_000)
  })

  it('filters candidate families according to the onboarding preference', () => {
    const area = searchSimulationAreas('수원 원천동')[0]
    const mixed = {
      ...baseResult,
      candidates: [
        { ...baseResult.candidates[0], candidate_id: 'independent-1', case_type: 'INDEPENDENT' as const, franchise: null, independent_model: { model_id: 'compact', adjusted_fields: [] } },
        { ...baseResult.candidates[0], candidate_id: 'franchise-1', case_type: 'FRANCHISE' as const, franchise: { brand_id: 'fixture', eligibility: 'VERIFIED' as const, availability_status: 'HQ_CONFIRMATION_REQUIRED' as const, eligibility_evidence_refs: [], disclosure_evidence_refs: [] }, independent_model: null },
      ],
    }
    const projected = buildSimulationResult(mixed, area, { ...values, cafeTypePreference: 'INDEPENDENT_ONLY' })
    expect(projected.candidates).toHaveLength(1)
    expect(projected.candidates[0].case_type).toBe('INDEPENDENT')
  })

  it('changes the simulated rent-sensitive finance range by area profile', () => {
    const woncheon = searchSimulationAreas('수원 원천동')[0]
    const gangnam = searchSimulationAreas('강남역')[0]
    const woncheonResult = buildSimulationResult(baseResult, woncheon, values)
    const gangnamResult = buildSimulationResult(baseResult, gangnam, { ...values, targetAreaInput: gangnam.display_name })
    expect(gangnamResult.candidates[0].financial_summary.monthly_fixed_cost.base)
      .toBeGreaterThan(woncheonResult.candidates[0].financial_summary.monthly_fixed_cost.base ?? 0)
  })
})