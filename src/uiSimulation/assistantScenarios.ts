import type {
  FeedbackPreview,
  ResultExplanation,
  ResultExplanationEvidence,
  ResultView,
} from '../apiClient'
import type { OnboardingValues } from '../onboardingState'
import { formatRange } from '../presentation'
import type { ResultCandidate } from '../resultContracts'

type ConditionField = 'own_funds_krw' | 'borrowing_intent' | 'cafe_type_preference' | 'operation_mode'

interface ConditionScenario {
  id: string
  matches: (input: string) => boolean
  field: ConditionField
  value: number | string
  apply: (values: OnboardingValues) => OnboardingValues
}

function normalize(input: string) {
  return input.trim().replace(/\s+/g, ' ')
}

const conditionScenarios: ConditionScenario[] = [
  {
    id: 'funds-100m',
    matches: (input) => /예산.*1억|1억.*예산|자기자금.*1억|1억.*바꿔/.test(input),
    field: 'own_funds_krw',
    value: 100_000_000,
    apply: (values) => ({ ...values, ownFundsKrw: '100000000' }),
  },
  {
    id: 'borrowing-yes',
    matches: (input) => /대출.*고려|대출.*가능|대출.*할게/.test(input),
    field: 'borrowing_intent',
    value: 'YES',
    apply: (values) => ({ ...values, borrowingIntent: 'YES' }),
  },
  {
    id: 'independent-only',
    matches: (input) => /프랜차이즈.*빼|프랜차이즈.*제외|개인카페만/.test(input),
    field: 'cafe_type_preference',
    value: 'INDEPENDENT_ONLY',
    apply: (values) => ({ ...values, cafeTypePreference: 'INDEPENDENT_ONLY' }),
  },
  {
    id: 'franchise-only',
    matches: (input) => /프랜차이즈만|브랜드만.*보고/.test(input),
    field: 'cafe_type_preference',
    value: 'FRANCHISE_ONLY',
    apply: (values) => ({ ...values, cafeTypePreference: 'FRANCHISE_ONLY' }),
  },
  {
    id: 'employee-led',
    matches: (input) => /직접 운영.*어려|직접.*운영.*힘들|직원.*운영/.test(input),
    field: 'operation_mode',
    value: 'EMPLOYEE_LED',
    apply: (values) => ({ ...values, operationMode: 'EMPLOYEE_LED' }),
  },
]

export function matchConditionScenario(input: string) {
  const normalized = normalize(input)
  return conditionScenarios.find((scenario) => scenario.matches(normalized)) ?? null
}

function founderProjection(values: OnboardingValues): Record<string, unknown> {
  return {
    target_area_input: values.targetAreaInput,
    own_funds_krw: Number(values.ownFundsKrw) || 0,
    borrowing_intent: values.borrowingIntent,
    cafe_type_preference: values.cafeTypePreference,
    operation_mode: values.operationMode,
    desired_opening_period: values.desiredOpeningPeriod,
    prior_cafe_experience: values.priorCafeExperience,
  }
}

function typedValue(value: unknown) {
  if (typeof value === 'number') return { kind: 'INTEGER', value }
  if (typeof value === 'string') return { kind: 'STRING', value }
  if (value == null) return { kind: 'NULL', value: null }
  throw new Error('조건 변경값의 타입을 확인하지 못했습니다.')
}

export function buildConditionPreview(
  input: string,
  result: ResultView,
  values: OnboardingValues,
): FeedbackPreview {
  const scenario = matchConditionScenario(input)
  if (!scenario) throw new Error('이 조건 변경 문구는 현재 준비된 시나리오가 아닙니다.')
  const nextValues = scenario.apply(values)
  const before = founderProjection(values)
  const after = founderProjection(nextValues)
  const fieldPath = `/founder/${scenario.field}`
  return {
    preview_id: `feedback-preview:${result.current_head.state_version + 1}:${scenario.id}`,
    project_id: result.project_id,
    result_bundle_id: result.result_bundle_id,
    head: result.current_head,
    status: 'REVIEW_REQUIRED',
    latest_user_input: normalize(input),
    before_founder: before,
    after_founder: after,
    operations: [{
      kind: 'SET',
      field_path: fieldPath,
      expected_old_value: typedValue(before[scenario.field]),
      typed_value: typedValue(scenario.value),
      semantic_kind: scenario.field === 'cafe_type_preference' || scenario.field === 'operation_mode'
        ? 'SOFT_PREFERENCE'
        : 'HARD_CONSTRAINT',
      ambiguity_codes: [],
    }],
    clarifying_questions: [],
    affected_stage_codes: ['RUN_PROPOSAL'],
    risk_flags: [],
    proposal_digest: `sha256:${'a'.repeat(64)}`,
  }
}

export function applyConditionScenario(input: string, values: OnboardingValues) {
  const scenario = matchConditionScenario(input)
  if (!scenario) throw new Error('이 조건 변경 문구는 현재 준비된 시나리오가 아닙니다.')
  return scenario.apply(values)
}

function formatWon(value: number | null | undefined) {
  return typeof value === 'number' ? `${value.toLocaleString('ko-KR')}원` : '확인 필요'
}

function candidateById(result: ResultView, candidateId?: string) {
  return result.candidates.find((candidate) => candidate.candidate_id === candidateId)
    ?? result.candidates.find((candidate) => candidate.candidate_id === result.primary_candidate_id)
    ?? result.candidates[0]
}

function firstComparison(result: ResultView, selected: ResultCandidate) {
  return [...result.candidates]
    .filter((candidate) => candidate.candidate_id !== selected.candidate_id)
    .sort((left, right) => (left.rank ?? 999) - (right.rank ?? 999))[0]
}

function evidenceFromCandidate(candidate: ResultCandidate, preferredField?: string): ResultExplanationEvidence[] {
  const ordered = [
    ...(preferredField
      ? (candidate.decision_inputs ?? []).filter((input) => input.field === preferredField)
      : []),
    ...(candidate.decision_inputs ?? []).filter((input) => input.field !== preferredField),
  ]
  const input = ordered.find((item) => item.source?.title)
  if (input?.source) {
    return [{
      evidence_id: input.range?.provenance_refs[0] ?? `evidence:${input.field}`,
      label: input.label ?? input.field,
      value: input.range ? formatRange(input.range) : null,
      source_title: input.source.title,
      source_ref: input.source.source_ref,
      data_date: input.source.data_date,
      caveat: input.limitation_code === 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY'
        ? '지역 참고값이며 실제 점포의 임대 조건은 아닙니다.'
        : null,
    }]
  }
  const document = candidate.official_documents?.[0]
  if (!document) return []
  return [{
    evidence_id: document.evidence_refs[0] ?? `evidence:${candidate.candidate_id}`,
    label: document.title,
    value: document.excerpt ?? null,
    source_title: document.title,
    source_ref: document.source_ref,
    data_date: document.data_date,
    caveat: null,
  }]
}

function gateReason(candidate: ResultCandidate) {
  const gate = candidate.decision_trace?.gates.find((item) => item.gate_type === 'CAPITAL')
  if (!gate) return '현재 자금 판정 근거를 추가로 확인해야 합니다.'
  if (gate.status === 'PASS') return '현재 자기자금이 초기 필요자금의 상단 시나리오까지 감당합니다.'
  if (gate.status === 'FAIL') {
    const shortfall = gate.metrics.shortfall_krw
    return typeof shortfall === 'number'
      ? `최소 초기 필요자금보다 자기자금이 ${formatWon(shortfall)} 부족합니다.`
      : '최소 초기 필요자금이 현재 자기자금을 넘습니다.'
  }
  return '초기 필요자금 범위가 현재 자기자금과 겹쳐 실제 점포·견적 확인이 필요합니다.'
}

function rankReason(candidate: ResultCandidate) {
  if (candidate.rank == null) return '현재는 자금 조건 때문에 순위 검토 대상에서 제외되어 있습니다.'
  const decisive = candidate.rank_trace?.decisive_factor
  if (decisive === 'FOUNDER_BURDEN') return `${candidate.rank}순위이며 운영 부담이 후보 간 우선순위를 갈랐습니다.`
  if (decisive === 'MONTHLY_FIXED_COST_BASE_KRW') return `${candidate.rank}순위이며 월 고정비가 후보 간 우선순위를 갈랐습니다.`
  if (decisive === 'INITIAL_CASH_BASE_KRW') return `${candidate.rank}순위이며 초기 필요자금이 후보 간 우선순위를 갈랐습니다.`
  return `${candidate.rank}순위로 다음 검토 우선순위에 들어 있습니다.`
}

function classifyExplanation(input: string): ResultExplanation['intent'] {
  if (/다른 후보|비교|뭐가 달라/.test(input)) return 'COMPARE'
  if (/출처|근거.*어디|어디서/.test(input)) return 'SOURCE'
  if (/확인 안|미확인|확인해야|아직.*뭐/.test(input)) return 'MISSING_INFO'
  if (/비싸지|달라지|어떻게 돼|어떻게 되/.test(input)) return 'COUNTERFACTUAL'
  if (/돈|비용|계산|예산.*위험/.test(input)) return 'FINANCE'
  return 'WHY_RECOMMENDED'
}

export function explainSimulationResult(
  question: string,
  result: ResultView,
  candidateId?: string,
): ResultExplanation {
  const selected = candidateById(result, candidateId)
  if (!selected) throw new Error('설명할 현재 결과가 없습니다.')
  const conditionScenario = matchConditionScenario(question)
  if (conditionScenario) {
    return {
      explanation_id: `explanation:${result.current_head.state_version}:condition`,
      result_bundle_id: result.result_bundle_id,
      candidate_id: selected.candidate_id,
      intent: 'OTHER',
      conclusion: '조건 변경 요청으로 확인했습니다.',
      reasons: [],
      evidence: [],
      unknowns: [],
      decision_change_conditions: [],
      suggested_action: 'OPEN_CONDITION_CHANGE',
      state_changed: false,
    }
  }

  const input = normalize(question)
  const intent = classifyExplanation(input)
  const comparison = firstComparison(result, selected)
  let conclusion = selected.summary
  let reasons: string[] = []
  let evidence = evidenceFromCandidate(selected)
  let unknowns = selected.next_actions
  let changeConditions = selected.counterfactuals.map((item) => item.condition)

  if (intent === 'WHY_RECOMMENDED') {
    conclusion = `${selected.display_name}을 현재 ${selected.rank ?? '검토'}순위로 보는 핵심은 자금 조건과 후보 간 비용·운영 부담 비교입니다.`
    reasons = [rankReason(selected), gateReason(selected)]
  } else if (intent === 'COMPARE') {
    conclusion = comparison
      ? `${selected.display_name}과 ${comparison.display_name}은 초기 필요자금과 월 고정비, 현재 검토 상태에서 차이가 납니다.`
      : `${selected.display_name}만 현재 비교 대상으로 남아 있습니다.`
    reasons = comparison ? [
      `${selected.display_name} 초기 필요자금은 ${formatRange(selected.financial_summary.initial_cash)}, ${comparison.display_name}은 ${formatRange(comparison.financial_summary.initial_cash)}입니다.`,
      `${selected.display_name} 월 고정비는 ${formatRange(selected.financial_summary.monthly_fixed_cost)}, ${comparison.display_name}은 ${formatRange(comparison.financial_summary.monthly_fixed_cost)}입니다.`,
      `${selected.display_name}은 ${selected.rank ?? '순위 제외'}, ${comparison.display_name}은 ${comparison.rank ?? '순위 제외'} 상태입니다.`,
    ] : [gateReason(selected)]
  } else if (intent === 'FINANCE') {
    const initial = selected.financial_summary.initial_cash
    const monthly = selected.financial_summary.monthly_fixed_cost
    conclusion = `${selected.display_name}의 초기 필요자금과 월 고정비를 현재 확인된 값·지역 참고값·등록 가정을 합쳐 계산했습니다.`
    reasons = [
      `초기 필요자금은 ${formatRange(initial)}입니다.`,
      `월 고정비는 ${formatRange(monthly)}, 손익분기 월매출 계산값은 ${formatWon(selected.financial_summary.break_even_monthly_sales_krw)}입니다.`,
      `손익분기 계산상 하루 필요 주문은 약 ${selected.financial_summary.required_daily_orders ?? '확인 필요'}건입니다.`,
    ]
    evidence = evidenceFromCandidate(selected, 'MONTHLY_OCCUPANCY')
  } else if (intent === 'SOURCE') {
    const sourceInput = (selected.decision_inputs ?? []).find((item) => item.field === 'MONTHLY_OCCUPANCY' && item.source)
      ?? (selected.decision_inputs ?? []).find((item) => item.source)
    conclusion = sourceInput?.source
      ? `${sourceInput.label ?? sourceInput.field}은 ${sourceInput.source.title}의 ${sourceInput.source.data_date ?? '현재 확인 기준'} 자료를 사용했습니다.`
      : '현재 선택 후보에서 바로 연결할 수 있는 공식 출처를 추가로 확인해야 합니다.'
    reasons = sourceInput?.source ? [
      sourceInput.source.geographic_scope
        ? `적용 범위는 ${sourceInput.source.geographic_scope}입니다.`
        : '해당 자료의 적용 범위를 함께 확인해야 합니다.',
      sourceInput.limitation_code === 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY'
        ? '실제 점포 계약값이 아니라 지역 참고값이므로 실제 매물 입력으로 교체할 수 있습니다.'
        : '현재 계산에 직접 사용된 확인값입니다.',
    ] : []
    evidence = evidenceFromCandidate(selected, 'MONTHLY_OCCUPANCY')
  } else if (intent === 'MISSING_INFO') {
    const requirements = selected.verification_requirements ?? []
    conclusion = `${selected.display_name}은 CaffeMate 안에서 확정할 수 없는 외부 확인 ${requirements.length}건과 실제값 정밀화 항목이 남아 있습니다.`
    reasons = [
      ...requirements.slice(0, 3).map((item) => `${item.label}: ${item.reason}`),
      ...selected.next_actions.slice(0, 2),
    ]
    unknowns = [
      ...requirements.map((item) => item.label),
      ...selected.next_actions,
    ]
  } else if (intent === 'COUNTERFACTUAL') {
    const first = selected.counterfactuals[0]
    conclusion = first
      ? `${first.condition} 현재 판단이 달라질 수 있습니다.`
      : '현재 결과에는 별도 판단 반전 조건이 등록되어 있지 않습니다.'
    reasons = first ? [first.decision_impact, gateReason(selected)] : [gateReason(selected)]
    evidence = evidenceFromCandidate(selected, first?.variable === 'MONTHLY_OCCUPANCY' ? 'MONTHLY_OCCUPANCY' : undefined)
    changeConditions = selected.counterfactuals.map((item) => `${item.condition} — ${item.decision_impact}`)
  }

  return {
    explanation_id: `explanation:${result.current_head.state_version}:${intent.toLowerCase()}`,
    result_bundle_id: result.result_bundle_id,
    candidate_id: selected.candidate_id,
    intent,
    conclusion,
    reasons,
    evidence,
    unknowns,
    decision_change_conditions: changeConditions,
    suggested_action: 'NONE',
    state_changed: false,
  }
}
