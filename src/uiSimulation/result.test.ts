import { describe, expect, it } from 'vitest'
import { result as baseResult, project as baseProject } from '../testSupport/appHarness'
import type { OnboardingValues } from '../onboardingState'
import { provenanceLabel } from '../result/resultPresentation'
import { buildSimulationProject, buildSimulationResult } from './result'
import { searchSimulationAreas, simulationAreaByToken } from './scenarios'

const values: OnboardingValues = {
  targetAreaInput: '성수',
  ownFundsKrw: '80000000',
  borrowingIntent: 'UNDECIDED',
  cafeTypePreference: 'OPEN_TO_BOTH',
  operationMode: 'DIRECT_FULL_TIME',
  desiredOpeningPeriod: '',
  priorCafeExperience: '',
}

function seongsu2() {
  const selected = searchSimulationAreas('성수').find((area) => area.display_name.endsWith('성수동2가'))
  if (!selected) throw new Error('missing Seongsu fixture')
  const area = simulationAreaByToken(selected.selection_token)
  if (!area) throw new Error('missing supported Seongsu analysis')
  return area
}

describe('Seongsu result projection', () => {
  it('projects the selected real area identity and founder inputs', () => {
    const projected = buildSimulationProject(baseProject, seongsu2(), values)
    expect(projected.state?.area.display_name).toBe('서울특별시 성동구 성수동2가')
    expect(projected.state?.area.coverage_profile).not.toMatch(/simulat|fixture|demo/i)
    expect(projected.state?.founder.own_funds_krw).toBe(80_000_000)
  })

  it('exposes the full Seoul market context as context-only evidence', () => {
    const projected = buildSimulationResult(baseResult, seongsu2(), values)
    const candidate = projected.candidates[0]
    expect(candidate.market_signals?.map((signal) => signal.signal_type)).toEqual([
      'CAFE_COUNT',
      'OPEN_COUNT',
      'CLOSE_COUNT',
      'CLOSURE_RATE',
      'ESTIMATED_SALES',
      'FOOT_TRAFFIC',
      'RESIDENT_POPULATION',
      'WORKER_POPULATION',
    ])
    expect(candidate.market_signals?.every((signal) => signal.decision_role === 'CONTEXT_ONLY')).toBe(true)
    expect(candidate.market_signals?.every((signal) => signal.source_title === '서울시 상권분석서비스')).toBe(true)
  })

  it('uses a REB regional occupancy benchmark and backend-shaped capital/rank traces', () => {
    const projected = buildSimulationResult(baseResult, seongsu2(), values)
    const candidate = projected.candidates[0]
    const occupancy = candidate.decision_inputs?.find((input) => input.field === 'MONTHLY_OCCUPANCY')
    expect(occupancy?.provenance).toBe('BENCHMARK')
    expect(occupancy?.resolution_status).toBe('RESOLVED_BENCHMARK')
    expect(occupancy?.source?.title).toBe('한국부동산원 상업용부동산 임대동향조사')
    expect(occupancy?.limitation_code).toBe('REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY')
    expect(candidate.decision_trace?.gates[0].reason_code).toBe('CAPITAL_COVERAGE_REQUIRES_CONFIRMATION')
    expect(candidate.rank_trace?.factors.map((factor) => factor.code)).toContain('FOUNDER_BURDEN')
  })

  it('separates grounded labor calculations from unresolved site-specific assumptions', () => {
    const projected = buildSimulationResult(baseResult, seongsu2(), values)
    const independent = projected.candidates.find((candidate) => candidate.case_type === 'INDEPENDENT')
    const labor = independent?.decision_inputs?.find((input) => input.field === 'MONTHLY_LABOR')
    const deposit = independent?.decision_inputs?.find((input) => input.field === 'DEPOSIT')
    const premium = independent?.decision_inputs?.find((input) => input.field === 'ACQUISITION_OR_PREMIUM')

    expect(labor).toMatchObject({
      provenance: 'DERIVED',
      resolution_status: 'RESOLVED_FACT',
      source: {
        title: '최저임금위원회·4대보험 관계기관 공식 기준',
        source_ref: 'https://www.minimumwage.go.kr/minWage/policy/decisionMain.do',
      },
    })
    expect(labor && provenanceLabel(labor)).toBe('계산값')
    expect(deposit).toMatchObject({ provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', source: null })
    expect(premium).toMatchObject({ provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', source: null })
  })

  it('presents accepted franchise disclosure costs as confirmed evidence', () => {
    const projected = buildSimulationResult(baseResult, seongsu2(), values)
    const franchise = projected.candidates.find((candidate) => candidate.case_type === 'FRANCHISE')
    const initialFees = franchise?.decision_inputs?.find((input) => input.field === 'FRANCHISE_INITIAL_FEES')
    const deposit = franchise?.decision_inputs?.find((input) => input.field === 'DEPOSIT')

    expect(initialFees).toMatchObject({
      provenance: 'FACT',
      resolution_status: 'RESOLVED_FACT',
      source: {
        title: '공정거래위원회 브랜드별 창업 금액 현황',
        source_ref: 'https://www.data.go.kr/data/15110265/openapi.do',
      },
    })
    expect(initialFees && provenanceLabel(initialFees)).toBe('확인된 자료')
    expect(deposit).toMatchObject({ provenance: 'ASSUMPTION', resolution_status: 'DECLARED_ASSUMPTION', source: null })
  })

  it('returns three production-like candidates and never leaks development markers in result payloads', () => {
    const projected = buildSimulationResult(baseResult, seongsu2(), values)
    expect(projected.candidates).toHaveLength(3)
    expect(projected.candidates.map((candidate) => candidate.display_name)).toEqual(expect.arrayContaining([
      '가치·속도 회전형 개인카페',
      '생활권 단골 균형형 개인카페',
      '이디야커피',
    ]))
    expect(JSON.stringify(projected)).not.toMatch(/ui[-_ ]?only|simulat|fixture|demo|개발 미리보기/i)
  })
})
