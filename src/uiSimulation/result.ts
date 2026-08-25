import type { OnboardingValues } from '../onboardingState'
import type { Project } from '../apiClient'
import type { DecisionGateTrace, DecisionInput, MoneyRange, ResultCandidate, ResultView } from '../resultContracts'
import type { SimulationAreaScenario } from './scenarios'

function scaled(value: number | null | undefined, multiplier: number) {
  return value == null ? null : Math.round(value * multiplier)
}

function shift(range: MoneyRange, delta: number): MoneyRange {
  const add = (value: number | null) => value == null ? null : Math.max(0, Math.round(value + delta))
  return {
    ...range,
    low: add(range.low),
    base: add(range.base),
    high: add(range.high),
    provenance_refs: ['ui-sim-regional-benchmark'],
  }
}

function projectDecisionInputs(
  inputs: DecisionInput[] | undefined,
  area: SimulationAreaScenario,
  ownFundsKrw: number,
): DecisionInput[] | undefined {
  return inputs?.map((input) => {
    if (input.field === 'own_funds_krw') return { ...input, value: ownFundsKrw }
    if (input.field !== 'monthly_occupancy_krw') return input
    return {
      ...input,
      range: input.range ? {
        ...input.range,
        low: scaled(input.range.low, area.rent_multiplier),
        base: scaled(input.range.base, area.rent_multiplier),
        high: scaled(input.range.high, area.rent_multiplier),
        provenance_refs: ['ui-sim-regional-benchmark'],
      } : input.range,
      source: {
        title: '지역 임차비 참고 범위 · UI 시뮬레이션',
        source_ref: 'ui-simulation://regional-rent',
        data_date: '2026-06-30',
        geographic_scope: area.rent_scope,
      },
    }
  })
}

function capitalGate(
  candidate: ResultCandidate,
  ownFundsKrw: number,
  minimumRequiredKrw: number | null,
  borrowingIntent: OnboardingValues['borrowingIntent'],
): { gate: DecisionGateTrace; reviewStatus: ResultCandidate['review_status']; reasonCodes: string[] } {
  const current = candidate.decision_trace?.gates.find((gate) => gate.gate_type === 'CAPITAL')
  if (minimumRequiredKrw == null || ownFundsKrw >= minimumRequiredKrw) {
    return {
      gate: {
        gate_type: 'CAPITAL',
        status: 'PASS',
        reason_code: 'CURRENT_CONSTRAINTS_SATISFIED',
        decisive_input_refs: current?.decisive_input_refs ?? ['own_funds_krw'],
        metrics: {
          own_funds_krw: ownFundsKrw,
          minimum_required_krw: minimumRequiredKrw,
          remaining_at_minimum_krw: minimumRequiredKrw == null ? null : ownFundsKrw - minimumRequiredKrw,
        },
      },
      reviewStatus: 'REVIEW_RECOMMENDED',
      reasonCodes: ['CURRENT_CONSTRAINTS_SATISFIED'],
    }
  }

  const shortfall = minimumRequiredKrw - ownFundsKrw
  if (borrowingIntent === 'NO') {
    return {
      gate: {
        gate_type: 'CAPITAL',
        status: 'FAIL',
        reason_code: 'MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS',
        decisive_input_refs: current?.decisive_input_refs ?? ['own_funds_krw'],
        metrics: { own_funds_krw: ownFundsKrw, minimum_required_krw: minimumRequiredKrw, shortfall_krw: shortfall },
      },
      reviewStatus: 'EXCLUDED',
      reasonCodes: ['MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS'],
    }
  }

  return {
    gate: {
      gate_type: 'CAPITAL',
      status: 'CONDITIONAL',
      reason_code: 'ADDITIONAL_FUNDING_REQUIRED',
      decisive_input_refs: current?.decisive_input_refs ?? ['own_funds_krw'],
      metrics: { own_funds_krw: ownFundsKrw, minimum_required_krw: minimumRequiredKrw, funding_gap_krw: shortfall },
    },
    reviewStatus: 'CONDITIONAL_REVIEW',
    reasonCodes: ['ADDITIONAL_FUNDING_REQUIRED'],
  }
}

function operationName(candidate: ResultCandidate, operationMode: OnboardingValues['operationMode']) {
  if (candidate.case_type !== 'INDEPENDENT' || !candidate.independent_model) return candidate.display_name
  if (candidate.independent_model.model_id === 'compact-takeout') {
    if (operationMode === 'EMPLOYEE_LED') return '소형 직원 운영형 개인카페'
    if (operationMode === 'DIRECT_PART_TIME') return '소형 시간제 참여형 개인카페'
  }
  return candidate.display_name
}

function projectCandidate(
  candidate: ResultCandidate,
  area: SimulationAreaScenario,
  values: OnboardingValues,
): ResultCandidate {
  const ownFundsKrw = Number(values.ownFundsKrw) || 0
  const rentFactorDelta = area.rent_multiplier - 1
  const initialCashDelta = Math.round(rentFactorDelta * 12_000_000)
  const monthlyFixedDelta = Math.round(rentFactorDelta * 1_500_000)
  const initialCash = shift(candidate.financial_summary.initial_cash, initialCashDelta)
  const monthlyFixedCost = shift(candidate.financial_summary.monthly_fixed_cost, monthlyFixedDelta)
  const minimumRequiredKrw = initialCash.low
  const capital = capitalGate(candidate, ownFundsKrw, minimumRequiredKrw, values.borrowingIntent)
  const nonCapitalGates = candidate.decision_trace?.gates.filter((gate) => gate.gate_type !== 'CAPITAL') ?? []

  const summary = capital.reviewStatus === 'EXCLUDED'
    ? `${area.display_name} 시뮬레이션 기준으로 현재 자기자금만으로는 최소 초기비용에 미치지 못합니다.`
    : capital.reviewStatus === 'CONDITIONAL_REVIEW'
      ? `${area.display_name} 시뮬레이션 기준으로 추가 자금 조건을 확인하면 다음 검토가 가능합니다.`
      : `${area.display_name}의 지역 참고 범위와 입력 조건을 적용한 UI 시뮬레이션 후보입니다.`

  return {
    ...candidate,
    display_name: operationName(candidate, values.operationMode),
    review_status: capital.reviewStatus,
    reason_codes: capital.reasonCodes,
    summary,
    financial_summary: {
      ...candidate.financial_summary,
      initial_cash: initialCash,
      monthly_fixed_cost: monthlyFixedCost,
    },
    market_signals: candidate.market_signals?.length ? [
      {
        ...candidate.market_signals[0],
        value: area.cafe_count,
        source_title: '지역 카페 현황 · UI 시뮬레이션',
        source_ref: 'ui-simulation://market/cafes',
        caveat: `${area.display_name} 화면 검토용 fixture이며 실제 상권 데이터가 아닙니다.`,
      },
      {
        ...(candidate.market_signals[1] ?? candidate.market_signals[0]),
        signal_type: 'FOOT_TRAFFIC',
        value: area.monthly_visits,
        unit: 'PERSON_VISITS_PER_MONTH_ESTIMATE',
        source_title: '지역 방문량 참고 · UI 시뮬레이션',
        source_ref: 'ui-simulation://market/visits',
        caveat: `${area.display_name} 화면 검토용 fixture이며 실제 유동인구 추정치가 아닙니다.`,
      },
    ] : candidate.market_signals,
    decision_inputs: projectDecisionInputs(candidate.decision_inputs, area, ownFundsKrw),
    decision_trace: { gates: [capital.gate, ...nonCapitalGates] },
  }
}

function allowsCandidate(candidate: ResultCandidate, preference: OnboardingValues['cafeTypePreference']) {
  if (preference === 'INDEPENDENT_ONLY') return candidate.case_type === 'INDEPENDENT'
  if (preference === 'FRANCHISE_ONLY') return candidate.case_type === 'FRANCHISE'
  return true
}

export function buildSimulationProject(
  base: Project,
  area: SimulationAreaScenario,
  values: OnboardingValues,
): Project {
  if (!base.state) throw new Error('UI_SIMULATION_PROJECT_STATE_REQUIRED')
  return {
    ...base,
    state: {
      ...base.state,
      founder: {
        own_funds_krw: Number(values.ownFundsKrw) || 0,
        borrowing_intent: values.borrowingIntent,
        cafe_type_preference: values.cafeTypePreference,
        operation_mode: values.operationMode,
      },
      area: {
        resolution_status: 'RESOLVED',
        area_id: area.area_id,
        scope_type: area.scope_type,
        legal_dong_code: area.legal_dong_code,
        administrative_dong_codes: area.administrative_dong_codes,
        mapping_status: area.mapping_status,
        display_name: area.display_name,
        coverage_profile: 'UI_SIMULATION',
        evidence_ids: ['ui-sim-area-evidence'],
        unavailable_fields: area.mapping_status === 'UNVERIFIED' ? ['administrative_dong_mapping'] : [],
      },
      updated_at: new Date().toISOString(),
    },
  }
}

export function buildSimulationResult(
  base: ResultView,
  area: SimulationAreaScenario,
  values: OnboardingValues,
): ResultView {
  const projected = base.candidates
    .filter((candidate) => allowsCandidate(candidate, values.cafeTypePreference))
    .map((candidate) => projectCandidate(candidate, area, values))
    .sort((left, right) => {
      const statusOrder = { REVIEW_RECOMMENDED: 0, CONDITIONAL_REVIEW: 1, EXCLUDED: 2 }
      const statusDiff = statusOrder[left.review_status] - statusOrder[right.review_status]
      if (statusDiff !== 0) return statusDiff
      return (left.financial_summary.initial_cash.base ?? Number.MAX_SAFE_INTEGER) - (right.financial_summary.initial_cash.base ?? Number.MAX_SAFE_INTEGER)
    })

  let rank = 0
  const candidates = projected.map((candidate) => {
    const reviewable = candidate.review_status !== 'EXCLUDED'
    const nextRank = reviewable ? ++rank : null
    return {
      ...candidate,
      rank: nextRank,
      rank_basis: reviewable ? 'UI_SIMULATION_NEXT_REVIEW_PRIORITY' : 'NOT_RANKED',
      is_primary_next_review: nextRank === 1,
    }
  })
  const primary = candidates.find((candidate) => candidate.is_primary_next_review) ?? null

  return {
    ...base,
    result_bundle_id: `ui-sim-result:${area.area_id}:${values.cafeTypePreference}:${values.operationMode}`,
    primary_candidate_id: primary?.candidate_id ?? null,
    outcome_status: primary ? 'REVIEWABLE_CANDIDATES' : 'NO_REVIEWABLE_CANDIDATES',
    candidates,
    decision_delta: null,
  }
}