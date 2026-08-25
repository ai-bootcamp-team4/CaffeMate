import type { DocumentExtractionForm, DocumentType, PropertyTermsInput } from '../apiClient'
import type { OnboardingValues } from '../onboardingState'
import type { DecisionInput, MoneyRange, ResultCandidate, ResultView } from '../resultContracts'
import { evaluateSimulationCapitalGate, rankSimulationCandidates } from './result'

function finiteRange(range: MoneyRange | null | undefined) {
  if (!range || range.low == null || range.base == null || range.high == null) {
    throw new Error('재계산할 비용 범위가 완전하지 않습니다.')
  }
  return { low: range.low, base: range.base, high: range.high }
}

function replaceRange(total: MoneyRange, previous: MoneyRange, exact: number, provenanceRef: string): MoneyRange {
  const current = finiteRange(total)
  const old = finiteRange(previous)
  return {
    currency: 'KRW',
    low: current.low - old.low + exact,
    base: current.base - old.base + exact,
    high: current.high - old.high + exact,
    provenance_refs: [...total.provenance_refs.filter((ref) => ref !== provenanceRef), provenanceRef],
  }
}

function findInput(candidate: ResultCandidate, field: string) {
  return candidate.decision_inputs?.find((input) => input.field === field) ?? null
}

function userConfirmedInput(
  previous: DecisionInput,
  amount: number,
  source: DecisionInput['source'],
): DecisionInput {
  return {
    ...previous,
    value: undefined,
    range: { currency: 'KRW', low: amount, base: amount, high: amount, provenance_refs: [source?.document_revision_id ?? 'user-confirmed-property'] },
    provenance: 'USER_INPUT',
    resolution_status: 'USER_CONFIRMED_FACT',
    source,
    replaceable_by: [],
    limitation_code: null,
    resolution_action: { type: 'NONE', target_fields: [] },
  }
}

function gateStatus(gate: ReturnType<typeof evaluateSimulationCapitalGate>): ResultCandidate['review_status'] {
  if (gate.status === 'FAIL') return 'EXCLUDED'
  if (gate.status === 'CONDITIONAL') return 'CONDITIONAL_REVIEW'
  return 'REVIEW_RECOMMENDED'
}

function recomputeBreakEven(candidate: ResultCandidate, monthlyFixedBase: number) {
  const previousMonthly = candidate.financial_summary.monthly_fixed_cost.base
  const previousBreakEven = candidate.financial_summary.break_even_monthly_sales_krw
  if (previousMonthly == null || previousMonthly <= 0 || previousBreakEven == null) return previousBreakEven ?? null
  return Math.ceil(previousBreakEven * monthlyFixedBase / previousMonthly)
}

function recomputeOrders(candidate: ResultCandidate, breakEvenSales: number | null) {
  const previousBreakEven = candidate.financial_summary.break_even_monthly_sales_krw
  const previousOrders = candidate.financial_summary.required_daily_orders
  if (breakEvenSales == null || previousBreakEven == null || previousBreakEven <= 0 || previousOrders == null) return previousOrders ?? null
  return Math.ceil(previousOrders * breakEvenSales / previousBreakEven * 100) / 100
}

function rerank(
  result: ResultView,
  previous: ResultCandidate,
  updated: ResultCandidate,
  inputChanges: NonNullable<ResultView['decision_delta']>['candidate_changes'][number]['input_changes'],
): ResultView {
  const beforeBundleId = result.result_bundle_id
  const beforePrimary = result.primary_candidate_id
  const previousGate = previous.decision_trace?.gates.find((gate) => gate.gate_type === 'CAPITAL') ?? null
  const currentGate = updated.decision_trace?.gates.find((gate) => gate.gate_type === 'CAPITAL') ?? null
  const ranked = rankSimulationCandidates(result.candidates.map((candidate) => candidate.candidate_id === previous.candidate_id ? updated : candidate))
  const currentCandidate = ranked.find((candidate) => candidate.candidate_id === previous.candidate_id) ?? updated
  const primary = ranked.find((candidate) => candidate.is_primary_next_review) ?? null
  const nextHead = {
    ...result.current_head,
    state_version: result.current_head.state_version + 1,
    workflow_generation: result.current_head.workflow_generation + 1,
  }
  const nextBundleId = `result-bundle:recalculated:${nextHead.state_version}:${nextHead.workflow_generation}`
  return {
    ...result,
    result_bundle_id: nextBundleId,
    candidates: ranked,
    primary_candidate_id: primary?.candidate_id ?? null,
    outcome_status: primary ? 'REVIEWABLE_CANDIDATES' : 'NO_REVIEWABLE_CANDIDATES',
    head: nextHead,
    current_head: nextHead,
    decision_delta: {
      previous_result_bundle_id: beforeBundleId,
      current_result_bundle_id: nextBundleId,
      primary_candidate_changed: beforePrimary !== primary?.candidate_id,
      requires_human_review: false,
      human_review_reason_codes: [],
      candidate_changes: [{
        candidate_key: `${previous.case_type}:${previous.independent_model?.model_id ?? previous.franchise?.brand_id ?? previous.candidate_id}`,
        display_name: previous.display_name,
        change_type: 'UPDATED',
        previous_rank: previous.rank,
        current_rank: currentCandidate.rank,
        previous_review_status: previous.review_status,
        current_review_status: currentCandidate.review_status,
        initial_cash_base_delta_krw: (currentCandidate.financial_summary.initial_cash.base ?? 0) - (previous.financial_summary.initial_cash.base ?? 0),
        monthly_fixed_cost_base_delta_krw: (currentCandidate.financial_summary.monthly_fixed_cost.base ?? 0) - (previous.financial_summary.monthly_fixed_cost.base ?? 0),
        break_even_monthly_sales_delta_krw: (currentCandidate.financial_summary.break_even_monthly_sales_krw ?? 0) - (previous.financial_summary.break_even_monthly_sales_krw ?? 0),
        reason_codes_added: currentCandidate.reason_codes.filter((code) => !previous.reason_codes.includes(code)),
        reason_codes_removed: previous.reason_codes.filter((code) => !currentCandidate.reason_codes.includes(code)),
        input_changes: inputChanges,
        gate_changes: previousGate?.status === currentGate?.status && previousGate?.reason_code === currentGate?.reason_code ? [] : [{
          gate_type: 'CAPITAL',
          previous_status: previousGate?.status ?? null,
          current_status: currentGate?.status ?? null,
          reason_code: currentGate?.reason_code ?? null,
        }],
      }],
    },
  }
}

function withFinanceAndGate(
  candidate: ResultCandidate,
  values: OnboardingValues,
  initialCash: MoneyRange,
  monthlyFixedCost: MoneyRange,
  decisionInputs: DecisionInput[],
): ResultCandidate {
  const initial = finiteRange(initialCash)
  const gate = evaluateSimulationCapitalGate(initial, values)
  const reviewStatus = gateStatus(gate)
  const monthlyBase = finiteRange(monthlyFixedCost).base
  const breakEven = recomputeBreakEven(candidate, monthlyBase)
  return {
    ...candidate,
    state_version: candidate.state_version + 1,
    review_status: reviewStatus,
    reason_codes: [gate.reason_code],
    summary: reviewStatus === 'EXCLUDED'
      ? '확인한 실제 비용을 반영하니 이 운영안의 최소 초기비용이 현재 자기자금을 넘습니다.'
      : reviewStatus === 'CONDITIONAL_REVIEW'
        ? '확인한 실제 비용을 반영했으며 자금 범위를 추가로 확인할 필요가 있습니다.'
        : '확인한 실제 비용을 반영해도 현재 자금 조건에서 다음 검토가 가능합니다.',
    financial_summary: {
      ...candidate.financial_summary,
      initial_cash: initialCash,
      monthly_fixed_cost: monthlyFixedCost,
      break_even_monthly_sales_krw: breakEven,
      required_daily_orders: recomputeOrders(candidate, breakEven),
    },
    decision_inputs: decisionInputs,
    decision_trace: { gates: [gate] },
  }
}

export function applyPropertyScenario(
  result: ResultView,
  candidateId: string,
  values: OnboardingValues,
  terms: PropertyTermsInput,
): ResultView {
  const selected = result.candidates.find((candidate) => candidate.candidate_id === candidateId)
  if (!selected) throw new Error('선택한 후보를 찾을 수 없습니다.')
  const deposit = findInput(selected, 'DEPOSIT')
  const premium = findInput(selected, 'ACQUISITION_OR_PREMIUM')
  const occupancy = findInput(selected, 'MONTHLY_OCCUPANCY')
  if (!deposit?.range || !occupancy?.range) throw new Error('점포 조건으로 교체할 비용 입력을 찾을 수 없습니다.')

  const propertySource: DecisionInput['source'] = {
    title: '사용자 확인 점포 조건',
    source_ref: null,
    data_date: '2026-08-25',
    geographic_scope: terms.address,
  }
  let initial = replaceRange(selected.financial_summary.initial_cash, deposit.range, terms.deposit_krw, 'user-confirmed-property')
  const replacements = new Map<string, DecisionInput>()
  replacements.set('DEPOSIT', userConfirmedInput(deposit, terms.deposit_krw, propertySource))
  if (premium?.range && terms.key_money_krw != null) {
    initial = replaceRange(initial, premium.range, terms.key_money_krw, 'user-confirmed-property')
    replacements.set('ACQUISITION_OR_PREMIUM', userConfirmedInput(premium, terms.key_money_krw, propertySource))
  }
  const occupancyAmount = terms.monthly_rent_krw + terms.management_fee_krw
  const monthly = replaceRange(selected.financial_summary.monthly_fixed_cost, occupancy.range, occupancyAmount, 'user-confirmed-property')
  replacements.set('MONTHLY_OCCUPANCY', userConfirmedInput(occupancy, occupancyAmount, propertySource))
  const decisionInputs = (selected.decision_inputs ?? []).map((input) => replacements.get(input.field) ?? input)
  const inputChanges = [...replacements.entries()].map(([field, after]) => {
    const before = findInput(selected, field)
    return { field, before, after, applied_to: after.applied_to }
  })
  const updated = withFinanceAndGate(selected, values, initial, monthly, decisionInputs)
  return rerank(result, selected, updated, inputChanges)
}

const documentTarget: Partial<Record<DocumentType, string>> = {
  EQUIPMENT_QUOTE: 'EQUIPMENT',
  INTERIOR_QUOTE: 'CONSTRUCTION',
  FRANCHISE_DISCLOSURE: 'FRANCHISE_INITIAL_FEES',
  FRANCHISE_AGREEMENT: 'FRANCHISE_INITIAL_FEES',
}

const documentTitle: Partial<Record<DocumentType, string>> = {
  EQUIPMENT_QUOTE: '장비 견적서',
  INTERIOR_QUOTE: '인테리어 견적서',
  FRANCHISE_DISCLOSURE: '프랜차이즈 정보공개서',
  FRANCHISE_AGREEMENT: '가맹계약서',
}

export function applyDocumentScenario(
  result: ResultView,
  candidateId: string,
  values: OnboardingValues,
  form: DocumentExtractionForm,
  documentType: DocumentType,
  filename: string,
): ResultView {
  const selected = result.candidates.find((candidate) => candidate.candidate_id === candidateId)
  if (!selected) throw new Error('선택한 후보를 찾을 수 없습니다.')
  const field = documentTarget[documentType]
  if (!field) throw new Error('이 문서 유형은 현재 비용 재계산에 연결되지 않습니다.')
  const previous = findInput(selected, field)
  const extracted = form.fields.find((item) => item.field_id === field)
  if (!previous?.range || typeof extracted?.current_value !== 'number') throw new Error('문서에서 재계산할 금액을 확인하지 못했습니다.')
  const source: DecisionInput['source'] = {
    title: documentTitle[documentType] ?? '사용자 확인 문서',
    source_ref: null,
    data_date: '2026-08-25',
    geographic_scope: '선택 후보',
    document_revision_id: form.document_revision_id,
    filename,
    page_index: extracted.anchor?.page_index ?? null,
    section_path: extracted.anchor?.section_path ?? null,
  }
  const after = userConfirmedInput(previous, extracted.current_value, source)
  const initial = replaceRange(selected.financial_summary.initial_cash, previous.range, extracted.current_value, form.document_revision_id)
  const decisionInputs = (selected.decision_inputs ?? []).map((input) => input.field === field ? after : input)
  const updated = withFinanceAndGate(selected, values, initial, selected.financial_summary.monthly_fixed_cost, decisionInputs)
  return rerank(result, selected, updated, [{ field, before: previous, after, applied_to: after.applied_to }])
}
