import type { OnboardingValues } from '../onboardingState'
import type { Project } from '../apiClient'
import type { DecisionGateTrace, DecisionInput, MoneyRange, ResultCandidate, ResultView } from '../resultContracts'
import type { SupportedAreaScenario } from './scenarios'
import { deriveOccupancyRange, independentSeeds, marketSignals, rebBenchmark, seongsuVerificationRequirements, type IndependentSeed, type SeedRange } from './seongsuData'

const INITIAL_FIELDS = ['DEPOSIT', 'ACQUISITION_OR_PREMIUM', 'CONSTRUCTION', 'EQUIPMENT', 'PREOPENING', 'OPENING_INVENTORY', 'CONTINGENCY', 'OPERATING_RESERVE']
const MONTHLY_FIELDS = ['MONTHLY_OCCUPANCY', 'MONTHLY_LABOR', 'MONTHLY_OTHER_FIXED']

const labels: Record<string, string> = {
  DEPOSIT: '보증금',
  ACQUISITION_OR_PREMIUM: '권리금·양수비',
  CONSTRUCTION: '인테리어비',
  EQUIPMENT: '장비비',
  PREOPENING: '개업 준비비',
  OPENING_INVENTORY: '초도 재고',
  CONTINGENCY: '예비비',
  OPERATING_RESERVE: '운영 준비금',
  MONTHLY_OCCUPANCY: '월 점유비',
  MONTHLY_LABOR: '월 인건비',
  MONTHLY_OTHER_FIXED: '월 기타 고정비',
  FRANCHISE_INITIAL_FEES: '가맹 초기비용',
}

function money(range: SeedRange, refs: string[]): MoneyRange {
  return { currency: 'KRW', ...range, provenance_refs: refs }
}

function sumRanges(ranges: SeedRange[]): SeedRange {
  return ranges.reduce((sum, value) => ({
    low: sum.low + value.low,
    base: sum.base + value.base,
    high: sum.high + value.high,
  }), { low: 0, base: 0, high: 0 })
}

function propertyAction(field: string): NonNullable<DecisionInput['resolution_action']> {
  if (field === 'DEPOSIT') return { type: 'PROPERTY_TERMS', target_fields: ['property.deposit_krw'] }
  if (field === 'ACQUISITION_OR_PREMIUM') return { type: 'PROPERTY_TERMS', target_fields: ['property.key_money_krw'] }
  if (field === 'MONTHLY_OCCUPANCY') return { type: 'PROPERTY_TERMS', target_fields: ['property.monthly_rent_krw', 'property.management_fee_krw'] }
  if (field === 'CONSTRUCTION') return { type: 'DOCUMENT_INTAKE', target_fields: ['finance.CONSTRUCTION'], accepted_document_types: ['INTERIOR_QUOTE'] }
  if (field === 'EQUIPMENT') return { type: 'DOCUMENT_INTAKE', target_fields: ['finance.EQUIPMENT'], accepted_document_types: ['EQUIPMENT_QUOTE'] }
  return { type: 'NONE', target_fields: [] }
}

function assumptionInput(field: string, range: SeedRange, seedId: string): DecisionInput {
  const action = propertyAction(field)
  return {
    field,
    label: labels[field],
    range: money(range, [`assumption:${seedId}:${field}`]),
    provenance: 'ASSUMPTION',
    resolution_status: 'DECLARED_ASSUMPTION',
    decision_role: 'FINANCE_INPUT',
    source: null,
    applied_to: MONTHLY_FIELDS.includes(field)
      ? ['MONTHLY_FIXED_COST', 'BREAK_EVEN_MONTHLY_SALES', 'REQUIRED_DAILY_ORDERS', 'RANK']
      : ['INITIAL_CASH', 'CAPITAL_GATE', 'RANK'],
    replaceable_by: action.type === 'NONE' ? [] : [action.type],
    limitation_code: 'REPLACE_WITH_CASE_DATA',
    resolution_action: action,
  }
}

function occupancyInput(seed: IndependentSeed, range: SeedRange): DecisionInput {
  return {
    field: 'MONTHLY_OCCUPANCY',
    label: labels.MONTHLY_OCCUPANCY,
    range: money(range, [`evidence-reb-occupancy:${seed.modelId}`]),
    provenance: 'BENCHMARK',
    resolution_status: 'RESOLVED_BENCHMARK',
    decision_role: 'FINANCE_INPUT',
    source: {
      title: rebBenchmark.sourceTitle,
      source_ref: rebBenchmark.sourceRef,
      data_date: rebBenchmark.dataDate,
      geographic_scope: rebBenchmark.geographicScope,
    },
    applied_to: ['MONTHLY_FIXED_COST', 'BREAK_EVEN_MONTHLY_SALES', 'REQUIRED_DAILY_ORDERS', 'RANK'],
    replaceable_by: ['PROPERTY_TERMS'],
    limitation_code: 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY',
    resolution_action: { type: 'PROPERTY_TERMS', target_fields: ['property.monthly_rent_krw', 'property.management_fee_krw'] },
  }
}

export function evaluateSimulationCapitalGate(initialCash: SeedRange, values: OnboardingValues): DecisionGateTrace {
  const ownFunds = Number(values.ownFundsKrw) || 0
  const metrics: Record<string, string | number | boolean | null> = {
    own_funds_krw: ownFunds,
    minimum_required_krw: initialCash.low,
    maximum_required_krw: initialCash.high,
    shortfall_krw: null,
  }
  if (initialCash.high <= ownFunds) {
    metrics.shortfall_krw = 0
    return {
      gate_type: 'CAPITAL',
      status: 'PASS',
      reason_code: 'OWN_FUNDS_COVER_HIGH_SCENARIO',
      decisive_input_refs: ['founder.own_funds_krw', 'finance.initial_cash.high'],
      metrics,
    }
  }
  if (values.borrowingIntent === 'NO' && initialCash.low > ownFunds) {
    const reduction = initialCash.low - ownFunds
    metrics.shortfall_krw = reduction
    metrics.minimum_required_reduction_krw = reduction
    return {
      gate_type: 'CAPITAL',
      status: 'FAIL',
      reason_code: 'MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS',
      decisive_input_refs: ['founder.borrowing_intent', 'founder.own_funds_krw', 'finance.initial_cash.low'],
      metrics,
    }
  }
  return {
    gate_type: 'CAPITAL',
    status: 'CONDITIONAL',
    reason_code: 'CAPITAL_COVERAGE_REQUIRES_CONFIRMATION',
    decisive_input_refs: ['founder.borrowing_intent', 'founder.own_funds_krw', 'finance.initial_cash.low', 'finance.initial_cash.high'],
    metrics,
  }
}

function statusForGate(gate: DecisionGateTrace): ResultCandidate['review_status'] {
  if (gate.status === 'FAIL') return 'EXCLUDED'
  if (gate.status === 'CONDITIONAL') return 'CONDITIONAL_REVIEW'
  return 'REVIEW_RECOMMENDED'
}

function breakEven(monthlyFixedBase: number, contributionMarginBps: number) {
  return Math.ceil(monthlyFixedBase * 10_000 / contributionMarginBps)
}

function dailyOrders(breakEvenSales: number, operatingDays: number, averageTicket: number) {
  return Math.ceil((breakEvenSales / operatingDays / averageTicket) * 100) / 100
}

function independentCandidate(seed: IndependentSeed, area: SupportedAreaScenario, values: OnboardingValues): ResultCandidate {
  const occupancy = deriveOccupancyRange(seed)
  const initialRanges = INITIAL_FIELDS.map((field) => seed.costs[field])
  const initialCash = sumRanges(initialRanges)
  const monthlyRanges = [occupancy, seed.costs.MONTHLY_LABOR, seed.costs.MONTHLY_OTHER_FIXED]
  const monthlyFixed = sumRanges(monthlyRanges)
  const gate = evaluateSimulationCapitalGate(initialCash, values)
  const status = statusForGate(gate)
  const breakEvenSales = breakEven(monthlyFixed.base, seed.contributionMarginBps)
  const inputs = [
    ...INITIAL_FIELDS.map((field) => assumptionInput(field, seed.costs[field], seed.modelId)),
    occupancyInput(seed, occupancy),
    assumptionInput('MONTHLY_LABOR', seed.costs.MONTHLY_LABOR, seed.modelId),
    assumptionInput('MONTHLY_OTHER_FIXED', seed.costs.MONTHLY_OTHER_FIXED, seed.modelId),
  ]
  return {
    candidate_id: `candidate:${seed.modelId}`,
    project_id: 'project:seongsu-review',
    state_version: 1,
    case_type: 'INDEPENDENT',
    display_name: seed.displayName,
    review_status: status,
    reason_codes: [gate.reason_code],
    summary: status === 'EXCLUDED'
      ? `${area.area.display_name}에서 이 운영안의 최소 초기비용이 현재 자기자금을 넘습니다.`
      : status === 'CONDITIONAL_REVIEW'
        ? `${area.area.display_name}의 지역 임차 참고값과 현재 자금 조건으로 다음 확인이 필요한 후보입니다.`
        : `${area.area.display_name}의 지역 임차 참고값과 현재 자금 조건에서 다음 검토 가치가 있는 후보입니다.`,
    rank: null,
    rank_basis: status === 'REVIEW_RECOMMENDED' ? 'ECONOMIC_AND_FOUNDER_FIT' : status === 'CONDITIONAL_REVIEW' ? 'NEXT_REVIEW_PRIORITY' : 'NOT_RANKED',
    is_primary_next_review: false,
    franchise: null,
    independent_model: { model_id: seed.modelId, adjusted_fields: [] },
    evidence_refs: marketSignals(area.analysis_key).map((signal) => signal.evidence_id),
    assumption_refs: [`assumption:${seed.modelId}`],
    market_signals: marketSignals(area.analysis_key),
    official_documents: [],
    official_document_gaps: ['실제 점포 임대 조건', '장비·인테리어 실제 견적'],
    financial_summary: {
      initial_cash: money(initialCash, [`assumption:${seed.modelId}:initial-cash`]),
      monthly_fixed_cost: money(monthlyFixed, [`evidence-reb-occupancy:${seed.modelId}`, `assumption:${seed.modelId}:monthly`]),
      break_even_monthly_sales_krw: breakEvenSales,
      required_daily_orders: dailyOrders(breakEvenSales, seed.operatingDaysPerMonth, seed.averageTicketKrw),
      unknown_cost_fields: [],
    },
    missing_fields: [],
    risks: [],
    counterfactuals: [{ variable: 'MONTHLY_OCCUPANCY', condition: '실제 점포 월세·관리비가 지역 참고 범위를 크게 넘을 경우', decision_impact: '월 고정비와 손익분기 매출, 후보 순위가 달라질 수 있습니다.' }],
    next_actions: ['실제 점포 임대 조건 확인', '장비·인테리어 견적 확인'],
    decision_inputs: inputs,
    decision_trace: { gates: [gate] },
    rank_trace: null,
    verification_requirements: seongsuVerificationRequirements('INDEPENDENT'),
  }
}

function ediyaCandidate(area: SupportedAreaScenario, values: OnboardingValues): ResultCandidate {
  const seedId = 'kr-ediya-coffee'
  const ranges: Record<string, SeedRange> = {
    DEPOSIT: { low: 30_000_000, base: 50_000_000, high: 80_000_000 },
    ACQUISITION_OR_PREMIUM: { low: 0, base: 15_000_000, high: 40_000_000 },
    CONSTRUCTION: { low: 38_000_000, base: 44_000_000, high: 52_000_000 },
    EQUIPMENT: { low: 34_000_000, base: 39_000_000, high: 45_000_000 },
    FRANCHISE_INITIAL_FEES: { low: 8_000_000, base: 11_000_000, high: 14_000_000 },
    PREOPENING: { low: 3_000_000, base: 5_000_000, high: 8_000_000 },
    OPENING_INVENTORY: { low: 2_000_000, base: 3_000_000, high: 5_000_000 },
    CONTINGENCY: { low: 10_000_000, base: 15_000_000, high: 25_000_000 },
    OPERATING_RESERVE: { low: 20_000_000, base: 30_000_000, high: 45_000_000 },
    MONTHLY_OCCUPANCY: { low: 4_000_000, base: 6_000_000, high: 9_000_000 },
    MONTHLY_LABOR: { low: 4_000_000, base: 6_000_000, high: 9_000_000 },
    MONTHLY_OTHER_FIXED: { low: 2_000_000, base: 3_000_000, high: 4_500_000 },
  }
  const initialCash = sumRanges(['DEPOSIT', 'ACQUISITION_OR_PREMIUM', 'CONSTRUCTION', 'EQUIPMENT', 'FRANCHISE_INITIAL_FEES', 'PREOPENING', 'OPENING_INVENTORY', 'CONTINGENCY', 'OPERATING_RESERVE'].map((field) => ranges[field]))
  const monthlyFixed = sumRanges(['MONTHLY_OCCUPANCY', 'MONTHLY_LABOR', 'MONTHLY_OTHER_FIXED'].map((field) => ranges[field]))
  const gate = evaluateSimulationCapitalGate(initialCash, values)
  const status = statusForGate(gate)
  const breakEvenSales = breakEven(monthlyFixed.base, 6500)
  const input = (field: string): DecisionInput => {
    const action: NonNullable<DecisionInput['resolution_action']> = field === 'FRANCHISE_INITIAL_FEES'
      ? { type: 'DOCUMENT_INTAKE', target_fields: ['finance.FRANCHISE_INITIAL_FEES'], accepted_document_types: ['FRANCHISE_DISCLOSURE', 'FRANCHISE_AGREEMENT'] }
      : propertyAction(field)
    const official = field === 'CONSTRUCTION' || field === 'EQUIPMENT'
    return {
      field,
      label: labels[field],
      range: money(ranges[field], [official ? `evidence-ediya:${field}` : `assumption:${seedId}:${field}`]),
      provenance: official ? 'FACT' : 'ASSUMPTION',
      resolution_status: official ? 'RESOLVED_FACT' : 'DECLARED_ASSUMPTION',
      decision_role: 'FINANCE_INPUT',
      source: official ? {
        title: '이디야커피 가맹점 개설 안내',
        source_ref: field === 'CONSTRUCTION' ? 'https://www.ediya.com/C/contents/interior.html' : 'https://www.ediya.com/C/contents/franchise_02.html',
        data_date: '2026-08-23',
        geographic_scope: '이디야커피 가맹 개설 안내 기준',
      } : null,
      applied_to: MONTHLY_FIELDS.includes(field) ? ['MONTHLY_FIXED_COST', 'BREAK_EVEN_MONTHLY_SALES', 'REQUIRED_DAILY_ORDERS', 'RANK'] : ['INITIAL_CASH', 'CAPITAL_GATE', 'RANK'],
      replaceable_by: action.type === 'NONE' ? [] : [action.type],
      limitation_code: official ? null : 'REPLACE_WITH_CASE_DATA',
      resolution_action: action,
    }
  }
  return {
    candidate_id: 'candidate:kr-ediya-coffee',
    project_id: 'project:seongsu-review',
    state_version: 1,
    case_type: 'FRANCHISE',
    display_name: '이디야커피',
    review_status: status,
    reason_codes: [gate.reason_code],
    summary: `${area.area.display_name} 후보의 경제 계산과 본사 확인 항목을 분리해 검토합니다.`,
    rank: null,
    rank_basis: status === 'REVIEW_RECOMMENDED' ? 'ECONOMIC_AND_FOUNDER_FIT' : status === 'CONDITIONAL_REVIEW' ? 'NEXT_REVIEW_PRIORITY' : 'NOT_RANKED',
    is_primary_next_review: false,
    franchise: {
      brand_id: seedId,
      eligibility: 'VERIFIED',
      availability_status: 'HQ_CONFIRMATION_REQUIRED',
      eligibility_evidence_refs: ['evidence-ediya-franchise-eligibility'],
      disclosure_evidence_refs: [],
    },
    independent_model: null,
    evidence_refs: ['evidence-ediya-franchise-eligibility', ...marketSignals(area.analysis_key).map((signal) => signal.evidence_id)],
    assumption_refs: [`assumption:${seedId}`],
    market_signals: marketSignals(area.analysis_key),
    official_documents: [{
      title: '이디야커피 가맹점 개설 안내',
      source_ref: 'https://www.ediya.com/C/contents/franchise_02.html',
      data_date: '2026-08-23',
      freshness_status: 'FRESH',
      document_version: '2026-08-23',
      excerpt: '가맹 개설 절차와 기본 비용 안내를 확인했습니다.',
      purposes: ['개인 가맹 가능 여부 확인', '공식 창업비 확인'],
      evidence_refs: ['evidence-ediya-franchise-eligibility'],
      used_in_candidate: true,
    }],
    official_document_gaps: ['최신 정보공개서 세부 가맹금'],
    financial_summary: {
      initial_cash: money(initialCash, [`assumption:${seedId}:initial-cash`]),
      monthly_fixed_cost: money(monthlyFixed, [`assumption:${seedId}:monthly`]),
      break_even_monthly_sales_krw: breakEvenSales,
      required_daily_orders: dailyOrders(breakEvenSales, 26, 7000),
      unknown_cost_fields: [],
    },
    missing_fields: [],
    risks: [{ risk_id: 'risk-franchise-cost-detail', severity: 'HIGH', summary: '가맹금 세부 항목은 최신 정보공개서로 교체할 수 있습니다.', evidence_refs: [] }],
    counterfactuals: [{ variable: 'FRANCHISE_INITIAL_FEES', condition: '최신 정보공개서의 가맹금이 현재 가정보다 높을 경우', decision_impact: '초기 필요자금과 자금 Gate가 달라질 수 있습니다.' }],
    next_actions: ['최신 정보공개서 비용 확인', '본사에 후보 주소 출점 가능 여부 확인'],
    decision_inputs: Object.keys(ranges).map(input),
    decision_trace: { gates: [gate] },
    rank_trace: null,
    verification_requirements: seongsuVerificationRequirements('FRANCHISE'),
  }
}

function founderBurden(candidate: ResultCandidate) {
  if (candidate.independent_model?.model_id === 'independent-small-takeout-v1') return 0
  return 1
}

function highRiskCount(candidate: ResultCandidate) {
  return candidate.risks.filter((risk) => risk.severity === 'HIGH').length
}

export function rankSimulationCandidates(candidates: ResultCandidate[]): ResultCandidate[] {
  const recommended = candidates.filter((candidate) => candidate.review_status === 'REVIEW_RECOMMENDED')
    .sort((a, b) => highRiskCount(a) - highRiskCount(b) || founderBurden(a) - founderBurden(b) || (a.financial_summary.monthly_fixed_cost.base ?? Number.MAX_SAFE_INTEGER) - (b.financial_summary.monthly_fixed_cost.base ?? Number.MAX_SAFE_INTEGER) || (a.financial_summary.initial_cash.base ?? Number.MAX_SAFE_INTEGER) - (b.financial_summary.initial_cash.base ?? Number.MAX_SAFE_INTEGER) || a.candidate_id.localeCompare(b.candidate_id))
  const conditional = candidates.filter((candidate) => candidate.review_status === 'CONDITIONAL_REVIEW')
    .sort((a, b) => highRiskCount(a) - highRiskCount(b) || founderBurden(a) - founderBurden(b) || a.candidate_id.localeCompare(b.candidate_id))
  const ordered = [...recommended, ...conditional]
  const ranked = ordered.map((candidate, index) => {
    const specs = candidate.review_status === 'REVIEW_RECOMMENDED'
      ? [
          { code: 'HIGH_RISK_COUNT', value: highRiskCount(candidate) },
          { code: 'FOUNDER_BURDEN', value: founderBurden(candidate) },
          { code: 'MONTHLY_FIXED_COST_BASE_KRW', value: candidate.financial_summary.monthly_fixed_cost.base },
          { code: 'INITIAL_CASH_BASE_KRW', value: candidate.financial_summary.initial_cash.base },
        ]
      : [
          { code: 'CRITICAL_RISK_COUNT', value: candidate.risks.filter((risk) => risk.severity === 'CRITICAL').length },
          { code: 'MATERIAL_GAP_COUNT', value: candidate.financial_summary.unknown_cost_fields.length },
          { code: 'HIGH_RISK_COUNT', value: highRiskCount(candidate) },
          { code: 'FOUNDER_BURDEN', value: founderBurden(candidate) },
        ]
    const neighbor = index === 0 ? ordered[1] : ordered[index - 1]
    const decisive = neighbor?.review_status !== candidate.review_status
      ? 'REVIEW_STATUS'
      : specs.find((factor) => {
          if (!neighbor) return false
          const other = factor.code === 'HIGH_RISK_COUNT' ? highRiskCount(neighbor)
            : factor.code === 'FOUNDER_BURDEN' ? founderBurden(neighbor)
              : factor.code === 'MONTHLY_FIXED_COST_BASE_KRW' ? neighbor.financial_summary.monthly_fixed_cost.base
                : factor.code === 'INITIAL_CASH_BASE_KRW' ? neighbor.financial_summary.initial_cash.base
                  : factor.code === 'CRITICAL_RISK_COUNT' ? neighbor.risks.filter((risk) => risk.severity === 'CRITICAL').length
                    : neighbor.financial_summary.unknown_cost_fields.length
          return factor.value !== other
        })?.code ?? null
    return {
      ...candidate,
      rank: index + 1,
      is_primary_next_review: index === 0,
      rank_trace: { basis: candidate.rank_basis, factors: specs, decisive_factor: decisive },
    }
  })
  return [...ranked, ...candidates.filter((candidate) => candidate.review_status === 'EXCLUDED').map((candidate) => ({ ...candidate, rank: null, is_primary_next_review: false, rank_trace: null }))]
}

export function buildSimulationProject(base: Project, area: SupportedAreaScenario, values: OnboardingValues): Project {
  if (!base.state) throw new Error('PROJECT_STATE_REQUIRED')
  return {
    ...base,
    project_id: 'project:seongsu-review',
    user_id: 'user:local-review',
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
        area_id: area.area.area_id,
        scope_type: area.area.scope_type,
        legal_dong_code: area.area.legal_dong_code,
        administrative_dong_codes: area.area.administrative_dong_codes,
        mapping_status: area.area.mapping_status,
        display_name: area.area.display_name,
        coverage_profile: 'R2_REGIONAL_CONNECTOR',
        evidence_ids: [`evidence-area:${area.area.legal_dong_code}`],
        unavailable_fields: area.area.mapping_status === 'UNVERIFIED' ? ['administrative_dong_mapping'] : [],
      },
      updated_at: new Date().toISOString(),
    },
  }
}

export function buildSimulationResult(base: ResultView, area: SupportedAreaScenario, values: OnboardingValues): ResultView {
  const ownFunds = Number(values.ownFundsKrw) || 0
  const operationMode = values.operationMode || 'UNDECIDED'
  const independent = independentSeeds
    .filter((seed) => seed.allowedOperationModes.includes(operationMode))
    .filter((seed) => seed.minimumOwnFundsKrw == null || ownFunds >= seed.minimumOwnFundsKrw)
    .map((seed) => independentCandidate(seed, area, values))
  const candidates = values.cafeTypePreference === 'FRANCHISE_ONLY'
    ? [ediyaCandidate(area, values)]
    : values.cafeTypePreference === 'INDEPENDENT_ONLY'
      ? independent
      : [...independent, ediyaCandidate(area, values)]
  const ranked = rankSimulationCandidates(candidates).slice(0, 3)
  const primary = ranked.find((candidate) => candidate.is_primary_next_review) ?? null
  const currentHead = {
    ...base.current_head,
    founder_snapshot_id: 'founder-snapshot:seongsu',
    area_snapshot_id: `area-snapshot:${area.area.legal_dong_code}`,
    evidence_snapshot_id: `evidence-snapshot:${area.analysis_key}`,
    seed_registry_id: 'independent-seeds:20260825',
  }
  return {
    ...base,
    project_id: 'project:seongsu-review',
    result_bundle_id: `result-bundle:${area.analysis_key}:v1`,
    workflow_run_id: 'workflow:first-proposal:seongsu',
    head: currentHead,
    current_head: currentHead,
    candidates: ranked.map((candidate) => ({ ...candidate, project_id: 'project:seongsu-review' })),
    primary_candidate_id: primary?.candidate_id ?? null,
    outcome_status: primary ? 'REVIEWABLE_CANDIDATES' : 'NO_REVIEWABLE_CANDIDATES',
    decision_delta: null,
  }
}
