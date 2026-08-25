import type { ResultCandidate } from '../resultContracts'
import type { SupportedAnalysisKey } from './scenarios'

export interface SeedRange {
  low: number
  base: number
  high: number
}

export interface IndependentSeed {
  modelId: string
  displayName: string
  allowedOperationModes: string[]
  minimumOwnFundsKrw: number | null
  founderBurden: 'LOW' | 'MEDIUM' | 'HIGH'
  contributionMarginBps: number
  operatingDaysPerMonth: number
  averageTicketKrw: number
  spaceProfileSqm: SeedRange
  deposit: SeedRange
  costs: Record<string, SeedRange>
}

export const independentSeeds: IndependentSeed[] = [
  {
    modelId: 'independent-small-takeout-v1',
    displayName: '가치·속도 회전형 개인카페',
    allowedOperationModes: ['DIRECT_FULL_TIME', 'DIRECT_PART_TIME', 'UNDECIDED'],
    minimumOwnFundsKrw: null,
    founderBurden: 'LOW',
    contributionMarginBps: 6800,
    operatingDaysPerMonth: 26,
    averageTicketKrw: 6500,
    spaceProfileSqm: { low: 20, base: 30, high: 40 },
    deposit: { low: 20_000_000, base: 35_000_000, high: 60_000_000 },
    costs: {
      DEPOSIT: { low: 20_000_000, base: 35_000_000, high: 60_000_000 },
      ACQUISITION_OR_PREMIUM: { low: 0, base: 10_000_000, high: 30_000_000 },
      CONSTRUCTION: { low: 20_000_000, base: 32_000_000, high: 48_000_000 },
      EQUIPMENT: { low: 18_000_000, base: 26_000_000, high: 38_000_000 },
      PREOPENING: { low: 2_000_000, base: 4_000_000, high: 6_000_000 },
      OPENING_INVENTORY: { low: 1_500_000, base: 2_500_000, high: 4_000_000 },
      CONTINGENCY: { low: 6_000_000, base: 10_000_000, high: 16_000_000 },
      OPERATING_RESERVE: { low: 12_000_000, base: 20_000_000, high: 30_000_000 },
      MONTHLY_LABOR: { low: 1_000_000, base: 2_500_000, high: 5_000_000 },
      MONTHLY_OTHER_FIXED: { low: 700_000, base: 1_100_000, high: 1_800_000 },
    },
  },
  {
    modelId: 'independent-balanced-v1',
    displayName: '생활권 단골 균형형 개인카페',
    allowedOperationModes: ['DIRECT_FULL_TIME', 'DIRECT_PART_TIME', 'EMPLOYEE_LED', 'UNDECIDED'],
    minimumOwnFundsKrw: 80_000_000,
    founderBurden: 'MEDIUM',
    contributionMarginBps: 6500,
    operatingDaysPerMonth: 26,
    averageTicketKrw: 7000,
    spaceProfileSqm: { low: 35, base: 50, high: 70 },
    deposit: { low: 30_000_000, base: 50_000_000, high: 80_000_000 },
    costs: {
      DEPOSIT: { low: 30_000_000, base: 50_000_000, high: 80_000_000 },
      ACQUISITION_OR_PREMIUM: { low: 0, base: 20_000_000, high: 50_000_000 },
      CONSTRUCTION: { low: 35_000_000, base: 55_000_000, high: 85_000_000 },
      EQUIPMENT: { low: 25_000_000, base: 38_000_000, high: 55_000_000 },
      PREOPENING: { low: 3_000_000, base: 5_000_000, high: 8_000_000 },
      OPENING_INVENTORY: { low: 2_000_000, base: 4_000_000, high: 6_000_000 },
      CONTINGENCY: { low: 10_000_000, base: 17_000_000, high: 28_000_000 },
      OPERATING_RESERVE: { low: 20_000_000, base: 30_000_000, high: 45_000_000 },
      MONTHLY_LABOR: { low: 2_500_000, base: 5_000_000, high: 9_000_000 },
      MONTHLY_OTHER_FIXED: { low: 1_000_000, base: 1_800_000, high: 3_000_000 },
    },
  },
]

const marketSources = {
  stores: 'https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do',
  sales: 'https://data.seoul.go.kr/dataList/OA-15572/A/1/datasetView.do',
  foot: 'https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do',
  resident: 'https://data.seoul.go.kr/dataList/OA-22182/S/1/datasetView.do',
  worker: 'https://data.seoul.go.kr/dataList/OA-22184/A/1/datasetView.do',
}

const marketValues: Record<SupportedAnalysisKey, Record<string, number>> = {
  SEOUL_SEONGSU_1GA: {
    CAFE_COUNT: 186,
    OPEN_COUNT: 19,
    CLOSE_COUNT: 11,
    CLOSURE_RATE: 5.9,
    ESTIMATED_SALES: 2_284_910_000,
    FOOT_TRAFFIC: 10_964_200,
    RESIDENT_POPULATION: 31_240,
    WORKER_POPULATION: 48_610,
  },
  SEOUL_SEONGSU_2GA: {
    CAFE_COUNT: 208,
    OPEN_COUNT: 23,
    CLOSE_COUNT: 14,
    CLOSURE_RATE: 6.7,
    ESTIMATED_SALES: 2_596_733_728,
    FOOT_TRAFFIC: 12_465_323,
    RESIDENT_POPULATION: 29_870,
    WORKER_POPULATION: 55_240,
  },
}

export function marketSignals(key: SupportedAnalysisKey): NonNullable<ResultCandidate['market_signals']> {
  const values = marketValues[key]
  const common = {
    data_date: '2026-03-31',
    freshness_status: 'FRESH' as const,
    source_title: '서울시 상권분석서비스',
    decision_role: 'CONTEXT_ONLY' as const,
  }
  return [
    { ...common, signal_type: 'CAFE_COUNT', value: values.CAFE_COUNT, unit: 'STORES', source_ref: marketSources.stores, evidence_id: `evidence-market-cafes-${key}`, caveat: '선택 지역에 연결된 행정동의 카페 업종 집계이며 개별 점포의 경쟁력을 뜻하지 않습니다.' },
    { ...common, signal_type: 'OPEN_COUNT', value: values.OPEN_COUNT, unit: 'STORES_PER_QUARTER', source_ref: marketSources.stores, evidence_id: `evidence-market-open-${key}`, caveat: '공식 신고 자료의 분기 신규 수치이며 실제 영업 시작 점포 수와 다를 수 있습니다.' },
    { ...common, signal_type: 'CLOSE_COUNT', value: values.CLOSE_COUNT, unit: 'STORES_PER_QUARTER', source_ref: marketSources.stores, evidence_id: `evidence-market-close-${key}`, caveat: '공식 신고 자료의 분기 폐업 수치이며 개별 점포의 생존확률을 뜻하지 않습니다.' },
    { ...common, signal_type: 'CLOSURE_RATE', value: values.CLOSURE_RATE, unit: 'PERCENT', source_ref: marketSources.stores, evidence_id: `evidence-market-closure-rate-${key}`, caveat: '현재 점포 수와 분기 폐업 수로 계산한 상권 변화 지표이며 생존확률이 아닙니다.' },
    { ...common, signal_type: 'ESTIMATED_SALES', value: values.ESTIMATED_SALES, unit: 'KRW_PER_QUARTER_ESTIMATE', source_ref: marketSources.sales, evidence_id: `evidence-market-sales-${key}`, caveat: '선택 지역의 카페 업종 분기 추정매출 합계이며 신규 점포 예상매출이 아닙니다.' },
    { ...common, signal_type: 'FOOT_TRAFFIC', value: values.FOOT_TRAFFIC, unit: 'PERSON_VISITS_PER_QUARTER_ESTIMATE', source_ref: marketSources.foot, evidence_id: `evidence-market-foot-${key}`, caveat: '선택 지역의 분기 추정 유동인구이며 고유 방문자 수가 아닙니다.' },
    { ...common, signal_type: 'RESIDENT_POPULATION', value: values.RESIDENT_POPULATION, unit: 'PERSONS', source_ref: marketSources.resident, evidence_id: `evidence-market-resident-${key}`, caveat: '선택 지역에 연결된 행정동의 거주인구 합계입니다.' },
    { ...common, signal_type: 'WORKER_POPULATION', value: values.WORKER_POPULATION, unit: 'PERSONS', source_ref: marketSources.worker, evidence_id: `evidence-market-worker-${key}`, caveat: '선택 지역에 연결된 행정동의 직장인구 합계입니다.' },
  ]
}

export const rebBenchmark = {
  effectiveRentKrwPerSqmMonth: 82_000,
  conversionRateBps: 550,
  managementFeeRatioBps: 1000,
  coverageStatus: 'PARENT_REGION',
  floorBasis: 'FIRST_FLOOR_NORMALIZED',
  sourceTitle: '한국부동산원 상업용부동산 임대동향조사',
  sourceRef: 'https://www.reb.or.kr',
  dataDate: '2026-06-30',
  geographicScope: '서울특별시 · 소규모 상가',
}

export function deriveOccupancyRange(seed: IndependentSeed): SeedRange {
  const depositCarry = seed.deposit.base * rebBenchmark.conversionRateBps / 10_000 / 12
  const occupancy = (areaSqm: number) => {
    const effective = rebBenchmark.effectiveRentKrwPerSqmMonth * areaSqm
    const estimatedRent = Math.max(0, effective - depositCarry)
    return Math.round(estimatedRent * (10_000 + rebBenchmark.managementFeeRatioBps) / 10_000)
  }
  return {
    low: occupancy(seed.spaceProfileSqm.low),
    base: occupancy(seed.spaceProfileSqm.base),
    high: occupancy(seed.spaceProfileSqm.high),
  }
}
