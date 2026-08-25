import { describe, expect, it } from 'vitest'
import type { OnboardingValues } from '../onboardingState'
import { result as baseResult } from '../testSupport/appHarness'
import { buildSimulationResult } from './result'
import { searchSimulationAreas, simulationAreaByToken } from './scenarios'
import {
  applyConditionScenario,
  buildConditionPreview,
  explainSimulationResult,
  matchConditionScenario,
} from './assistantScenarios'

const values: OnboardingValues = {
  targetAreaInput: '성수',
  ownFundsKrw: '150000000',
  borrowingIntent: 'NO',
  cafeTypePreference: 'OPEN_TO_BOTH',
  operationMode: 'DIRECT_FULL_TIME',
  desiredOpeningPeriod: '',
  priorCafeExperience: '',
}

function scenarioResult() {
  const selected = searchSimulationAreas('성수').find((area) => area.display_name.endsWith('성수동2가'))
  if (!selected) throw new Error('missing Seongsu search result')
  const area = simulationAreaByToken(selected.selection_token)
  if (!area) throw new Error('missing Seongsu scenario')
  return { area, result: buildSimulationResult(baseResult, area, values) }
}

describe('result assistant scenarios', () => {
  it.each([
    ['왜 이 안을 먼저 보나요?', 'WHY_RECOMMENDED'],
    ['다른 후보랑 뭐가 달라?', 'COMPARE'],
    ['돈이 어떻게 계산됐어?', 'FINANCE'],
    ['월 점유비 출처가 뭐야?', 'SOURCE'],
    ['아직 확인 안 된 게 뭐야?', 'MISSING_INFO'],
    ['월세가 더 비싸지면 어떻게 돼?', 'COUNTERFACTUAL'],
  ] as const)('routes %s to %s and answers from the current result', (question, intent) => {
    const { result } = scenarioResult()
    const answer = explainSimulationResult(question, result, result.primary_candidate_id ?? undefined)

    expect(answer.intent).toBe(intent)
    expect(answer.suggested_action).toBe('NONE')
    expect(answer.state_changed).toBe(false)
    expect(answer.conclusion.length).toBeGreaterThan(10)
    expect(JSON.stringify(answer)).not.toMatch(/ui[-_ ]?only|simulat|fixture|demo/i)
  })

  it.each([
    ['예산을 1억으로 바꿔줘', 'own_funds_krw', 100_000_000],
    ['대출도 고려할게', 'borrowing_intent', 'YES'],
    ['프랜차이즈는 빼줘', 'cafe_type_preference', 'INDEPENDENT_ONLY'],
    ['프랜차이즈만 보고 싶어', 'cafe_type_preference', 'FRANCHISE_ONLY'],
    ['직접 운영은 어려워', 'operation_mode', 'EMPLOYEE_LED'],
  ] as const)('creates a production-shaped preview for %s', (input, field, expected) => {
    const { result } = scenarioResult()
    const matched = matchConditionScenario(input)
    expect(matched).not.toBeNull()

    const preview = buildConditionPreview(input, result, values)
    expect(preview.status).toBe('REVIEW_REQUIRED')
    expect(preview.before_founder[field]).not.toBe(expected)
    expect(preview.after_founder?.[field]).toBe(expected)
    expect(preview.operations).toHaveLength(1)
    expect(preview.proposal_digest).toMatch(/^sha256:/)

    const changed = applyConditionScenario(input, values)
    expect(changed).not.toEqual(values)
  })

  it('rebuilds candidate composition from the changed founder values', () => {
    const { area, result } = scenarioResult()
    const independentOnly = applyConditionScenario('프랜차이즈는 빼줘', values)
    const next = buildSimulationResult(result, area, independentOnly)
    expect(next.candidates.every((candidate) => candidate.case_type === 'INDEPENDENT')).toBe(true)

    const franchiseOnly = applyConditionScenario('프랜차이즈만 보고 싶어', values)
    const franchiseResult = buildSimulationResult(result, area, franchiseOnly)
    expect(franchiseResult.candidates).toHaveLength(1)
    expect(franchiseResult.candidates[0].case_type).toBe('FRANCHISE')
  })

  it('shows source-backed financial evidence as a range without a base amount', () => {
    const { result } = scenarioResult()
    const answer = explainSimulationResult('월 점유비 출처가 뭐야?', result, result.primary_candidate_id ?? undefined)

    expect(answer.evidence[0]?.value).toMatch(/^\d[\d,]*원(?: ~ \d[\d,]*원)?$/)
    expect(answer.evidence[0]?.value).not.toContain('기준')
  })
})
