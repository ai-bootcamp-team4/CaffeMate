import type { DecisionGateTrace, DecisionInput, ResultCandidate } from '../apiClient'
import { formatRange, formatWon, internalLabel } from '../presentation'

export function publicStatus(status: ResultCandidate['review_status']) {
  if (status === 'REVIEW_RECOMMENDED') return '검토 추천'
  if (status === 'CONDITIONAL_REVIEW') return '조건부 검토'
  return '현재 조건에서 어려움'
}

export function decisionHeading(status: ResultCandidate['review_status']) {
  return status === 'EXCLUDED'
    ? '왜 이 안은 지금 진행하기 어려운가요?'
    : '왜 이 안을 검토할 수 있나요?'
}

export function conclusionCopy(candidate: ResultCandidate) {
  const gates = candidate.decision_trace?.gates ?? []
  const hasFail = gates.some((gate) => gate.status === 'FAIL')
  const hasExternal = (candidate.verification_requirements?.length ?? 0) > 0
  if (candidate.review_status === 'EXCLUDED' || hasFail) {
    return '현재 확인된 조건 중 이 안을 막는 항목이 있어요. 아래 판정 이유와 숫자를 먼저 확인하세요.'
  }
  if (candidate.review_status === 'CONDITIONAL_REVIEW' && hasExternal) {
    return 'CaffeMate가 계산한 조건과 외부에서만 확정할 수 있는 조건을 분리해서 확인하세요.'
  }
  if (candidate.review_status === 'CONDITIONAL_REVIEW') {
    return '현재 계산은 이어갈 수 있지만, 실제 자료를 넣어야 확정할 값이 남아 있어요.'
  }
  return '현재 조건에서는 실제 점포와 견적을 넣어 더 구체적으로 검증할 단계예요.'
}

export function gateTitle(gate: DecisionGateTrace) {
  if (gate.gate_type === 'CAPITAL') return '자금 조건'
  return internalLabel(gate.gate_type, '필수 조건')
}

export function gateCopy(gate: DecisionGateTrace) {
  const title = gateTitle(gate)
  if (gate.status === 'PASS') return `${title}은 현재 계산에서 통과했어요.`
  if (gate.status === 'FAIL') return `${title}이 현재 판단을 막고 있어요.`
  return `${title}은 추가 정보가 있어야 확정할 수 있어요.`
}

export function gateMetricRows(gate: DecisionGateTrace) {
  const labels: Record<string, string> = {
    own_funds_krw: '현재 자기자금',
    minimum_required_krw: '최소 필요자금',
    shortfall_krw: '최소 부족액',
    minimum_required_reduction_krw: '줄여야 할 최소 금액',
    remaining_at_minimum_krw: '최소 비용 기준 남는 금액',
  }
  return Object.entries(gate.metrics)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => ({
      label: labels[key] ?? internalLabel(key, '판정 지표'),
      value: key.endsWith('_krw') && typeof value === 'number' ? formatWon(value) : String(value),
    }))
}

export function decisionInputLabel(input: DecisionInput) {
  return input.label?.trim() || internalLabel(input.field, '판단 입력값')
}

export function decisionInputValue(input: DecisionInput) {
  if (input.range) return formatRange(input.range)
  if (typeof input.value === 'number') {
    return input.field.endsWith('_krw') || input.field.includes('cost') || input.field.includes('cash')
      ? formatWon(input.value)
      : input.value.toLocaleString('ko-KR')
  }
  if (typeof input.value === 'boolean') return input.value ? '예' : '아니요'
  if (typeof input.value === 'string' && input.value.trim()) return input.value
  return '아직 값이 없습니다'
}

export function provenanceLabel(input: DecisionInput) {
  const byResolution: Partial<Record<DecisionInput['resolution_status'], string>> = {
    USER_CONFIRMED_FACT: '실제 입력',
    RESOLVED_BENCHMARK: '지역 참고값',
    DECLARED_ASSUMPTION: '참고 가정',
    DOCUMENT_REQUIRED: '문서 확인 필요',
    INPUT_REQUIRED: '실제 입력 필요',
    EXTERNAL_CONFIRMATION_REQUIRED: '외부 확인',
    UNSUPPORTED_BY_DATA: '데이터로 판정 불가',
  }
  if (byResolution[input.resolution_status]) return byResolution[input.resolution_status]!
  return {
    FACT: '확인된 자료',
    USER_INPUT: '실제 입력',
    BENCHMARK: '공식 참고값',
    ASSUMPTION: '참고 가정',
    DERIVED: '계산값',
    UNKNOWN: '미확인',
  }[input.provenance]
}

export function limitationCopy(code: string | null) {
  if (!code) return null
  if (code === 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY') {
    return '지역 참고값이며 실제 점포의 임대 조건은 아닙니다.'
  }
  return '적용 범위에 제한이 있는 자료입니다. 실제 조건으로 교체할 수 있어요.'
}

export function refinableInputs(candidate: ResultCandidate) {
  return (candidate.decision_inputs ?? []).filter((input) =>
    ['PROPERTY_TERMS', 'DOCUMENT_INTAKE', 'USER_INPUT'].includes(input.resolution_action?.type ?? ''),
  ).filter((input) => !['USER_CONFIRMED_FACT', 'RESOLVED_FACT'].includes(input.resolution_status))
}

export function financeInputs(candidate: ResultCandidate) {
  return (candidate.decision_inputs ?? []).filter((input) => input.decision_role === 'FINANCE_INPUT')
}

export function resolutionStatusLabel(status: DecisionInput['resolution_status']) {
  return {
    RESOLVED_FACT: '확인된 사실',
    USER_CONFIRMED_FACT: '실제 입력으로 확인',
    RESOLVED_BENCHMARK: '지역 참고값',
    DECLARED_ASSUMPTION: '참고 가정',
    INPUT_REQUIRED: '실제 입력 필요',
    DOCUMENT_REQUIRED: '문서 확인 필요',
    EXTERNAL_CONFIRMATION_REQUIRED: '외부 확인 필요',
    UNSUPPORTED_BY_DATA: '데이터로 판정할 수 없음',
  }[status]
}

export function rankFactorLabel(candidate: ResultCandidate) {
  const decisive = candidate.rank_trace?.decisive_factor
  if (!decisive) return null
  const factor = candidate.rank_trace?.factors.find((value) => value.code === decisive)
  const labels: Record<string, string> = {
    INITIAL_CASH_BASE: '초기 필요자금',
    MONTHLY_FIXED_COST_BASE: '월 고정비',
    HIGH_RISK_COUNT: '높은 위험 수',
    FOUNDER_BURDEN: '운영 부담',
  }
  return {
    label: labels[decisive] ?? internalLabel(decisive, '비교 기준'),
    value: typeof factor?.value === 'number' && decisive.includes('CASH')
      ? formatWon(factor.value)
      : factor?.value == null ? null : String(factor.value),
  }
}
