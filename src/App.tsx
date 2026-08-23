import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import Onboarding from './Onboarding'
import Welcome from './Welcome'
import { createFirebaseAuthGateway, type AuthGateway, type AuthSession } from './auth'
import {
  createControlApiClient,
  waitForWorkflow,
  type CandidateSelection,
  type ControlApiClient,
  type FeedbackPreview,
  type PreparationGuide,
  type PropertyTermsApplication,
  type PropertyTermsInput,
  type Project,
  type ResultCandidate,
  type ResultView,
  type WorkflowProgress,
} from './apiClient'
import type { OnboardingValues } from './onboardingState'

type PanelName = 'overview' | 'market' | 'franchise' | 'funds' | 'risks'
type AppScreen = 'welcome' | 'onboarding' | 'result'

const panels: Array<{ id: PanelName; label: string }> = [
  { id: 'overview', label: '판단 요약' },
  { id: 'market', label: '상권 신호' },
  { id: 'franchise', label: '가맹 조건' },
  { id: 'funds', label: '필요자금' },
  { id: 'risks', label: '위험과 검증' },
]

const internalLabels: Record<string, string> = {
  REVIEW_RECOMMENDED: '검토 추천',
  CONDITIONAL_REVIEW: '조건부 검토',
  EXCLUDED: '현재 검토에서 제외',
  CURRENT_CONSTRAINTS_SATISFIED: '현재 입력 조건을 충족함',
  INITIAL_CASH_LOW_UNKNOWN: '최소 창업비 확인 필요',
  OWN_FUNDS_COVER_HIGH_SCENARIO: '보유 자금으로 높은 비용 범위까지 충당 가능',
  MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS: '최소 창업비가 보유 자금을 초과함',
  CAPITAL_COVERAGE_REQUIRES_CONFIRMATION: '자금 충당 가능 범위 확인 필요',
  MATERIAL_COST_UNKNOWN: '핵심 비용 확인 필요',
  MATERIAL_FIELD_MISSING: '핵심 정보 확인 필요',
  FOUNDER_FIT_HARD_CONFLICT: '운영 방식과 창업자 조건이 맞지 않음',
  FOUNDER_FIT_REQUIRES_CONFIRMATION: '운영 적합도 확인 필요',
  CRITICAL_RISK_REQUIRES_REVIEW: '중대한 위험 검토 필요',
  FRANCHISE_INELIGIBLE: '개인 가맹 대상이 아님',
  FRANCHISE_ELIGIBILITY_UNVERIFIED: '개인 가맹 자격 확인 필요',
  FRANCHISE_UNAVAILABLE_IN_AREA: '희망 지역에 출점할 수 없음',
  FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED: '희망 지역 출점 가능 여부 확인 필요',
  HQ_CONFIRMATION_REQUIRED: '본사 확인 필요',
  AVAILABLE: '가능',
  UNAVAILABLE: '불가',
  UNKNOWN: '확인되지 않음',
  VERIFIED: '확인 완료',
  UNVERIFIED: '확인 필요',
  INELIGIBLE: '대상 아님',
  RESOLVED: '지역 확인 완료',
  UNRESOLVED: '지역 확인 필요',
  N0_NATIONWIDE_FACTS: '전국 기준 자료 없음',
  NO_NATIONWIDE_FACTS: '전국 기준 자료 없음',
  N1_NATIONWIDE_CONDITIONAL: '전국 조건부 기준 자료',
  R2_REGIONAL_CONNECTOR: '지역 데이터 연결됨',
  C3_CASE_ARTIFACT: '사용자 제출 자료 반영',
  ACQUISITION_OR_PREMIUM: '권리금·영업권',
  CONSTRUCTION: '인테리어·시설 공사비',
  CONTINGENCY: '예비비',
  DEPOSIT: '임차 보증금',
  EQUIPMENT: '장비비',
  FRANCHISE_INITIAL_FEES: '가맹 초기 비용',
  MONTHLY_LABOR: '월 인건비',
  MONTHLY_OCCUPANCY: '월 임차료·관리비',
  MONTHLY_OTHER_FIXED: '기타 월 고정비',
  OPENING_INVENTORY: '오픈 초기 재고',
  OPERATING_RESERVE: '운영 예비자금',
  PREOPENING: '개업 전 준비비',
  premium: '권리금·영업권',
  royalty: '로열티',
  area_availability_hq_confirmation: '희망 지역 출점 가능 여부',
  franchise_disclosure: '정보공개서',
  franchise_disclosure_freshness: '최신 정보공개서',
  administrative_dong_mapping: '행정동 연결 정보',
  estimated_store_sales: '점포 추정 매출',
  'operations.menu_complexity': '메뉴 구성 복잡도',
  'operations.open_hours_per_day': '하루 영업시간',
  'operations.owner_hours_per_week': '창업자 주간 근무시간',
  'operations.staff_count': '직원 수',
  target_area_input: '희망 지역',
  own_funds_krw: '자기자금',
  borrowing_intent: '대출 활용 의향',
  cafe_type_preference: '창업 유형',
  operation_mode: '운영 방식',
  desired_opening_period: '희망 개업 시기',
  prior_cafe_experience: '카페 운영 경험',
  preferences: '선호 조건',
  avoidances: '제외 조건',
  initial_cash_krw: '초기 필요자금',
  PASSED: '통과',
  REQUIRES_HUMAN: '사람 확인 필요',
  CURRENT: '현재 기준',
  STALE: '이전 기준',
  PROCESSING: '처리 중',
  REVIEW_REQUIRED: '변경 확인 필요',
  CLARIFICATION_REQUIRED: '추가 설명 필요',
  NOOP: '변경 없음',
  UNSUPPORTED: '지원하지 않는 요청',
  EXPIRED: '확인 기한 만료',
  CONFIRMED: '반영 완료',
  CANCELLED: '취소됨',
  QUEUED: '분석 대기 중',
  RUNNING: '분석 중',
  WAITING_FOR_HUMAN: '추가 확인 필요',
  SUCCEEDED: '분석 완료',
  PARTIAL: '일부 정보로 분석 완료',
  FAILED: '분석 실패',
  AREA_RESOLUTION: '희망 지역 확인',
  CLAIM_PLAN: '확인할 정보 정리',
  EVIDENCE_PLAN: '자료 조사 계획',
  EVIDENCE_RETRIEVAL: '자료 조회',
  EVIDENCE_ASSESS: '자료 신뢰도 검토',
  EVIDENCE_FREEZE: '근거 확정',
  INDEPENDENT_SEED: '개인카페 운영안 준비',
  FRANCHISE_ELIGIBILITY: '가맹 후보 확인',
  PROPOSE_INDEPENDENT: '개인카페 제안 작성',
  PROPOSE_FRANCHISE: '프랜차이즈 제안 작성',
  CALCULATE_GATE_RANK: '비용·조건 비교',
  CANDIDATE_AUDIT: '후보 독립 검토',
  COMMIT_RESULT: '결과 확정',
}

function internalLabel(value: string, fallback = '추가 확인 필요') {
  const known = internalLabels[value]
  if (known) return known
  if (value.startsWith('/founder/')) return internalLabels[value.slice('/founder/'.length)] ?? '창업 조건'
  const looksInternal = /^[A-Z][A-Z0-9_]{2,}$/.test(value)
    || /^[a-z][a-z0-9]*(?:[._][a-z0-9_]+)+$/.test(value)
    || /^(risk|candidate|proposal|evidence|assumption|brand)-[a-z0-9-]+$/i.test(value)
  return looksInternal ? fallback : value
}

function displayText(value: string) {
  let next = value
  for (const [internal, label] of Object.entries(internalLabels).sort(([left], [right]) => right.length - left.length)) {
    next = next.replaceAll(internal, label)
  }
  return next
    .replace(/\b[A-Z][A-Z0-9_]{2,}\b/g, '추가 확인 항목')
    .replace(/\b[a-z][a-z0-9]*(?:[._][a-z0-9_]+)+\b/g, '추가 확인 항목')
    .replace(/\b(?:risk|candidate|proposal|evidence|assumption|brand)-[a-z0-9-]+\b/gi, '추가 확인 항목')
}

function userError(error: unknown, fallback: string) {
  return error instanceof Error ? displayText(error.message) : fallback
}

function displayValue(value: unknown): string {
  if (value == null || value === '') return '없음'
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  if (typeof value === 'string') return internalLabel(value)
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  if (Array.isArray(value)) return value.map(displayValue).join(' · ') || '없음'
  return '변경됨'
}

function uniqueLabels(values: string[], fallback?: string) {
  return [...new Set(values.map((value) => internalLabel(value, fallback)))]
}

function severityLabel(severity: ResultCandidate['risks'][number]['severity']) {
  return { LOW: '낮은 위험', MEDIUM: '보통 위험', HIGH: '높은 위험', CRITICAL: '중대한 위험' }[severity]
}

function Badge({ children, tone = '' }: { children: React.ReactNode; tone?: string }) {
  return <span className={`badge ${tone ? `badge--${tone}` : ''}`}>{children}</span>
}

function formatWon(value: number | null | undefined) {
  return value == null ? '확인되지 않음' : `${new Intl.NumberFormat('ko-KR').format(value)}원`
}

function formatRange(range: ResultCandidate['financial_summary']['initial_cash']) {
  if (range.low == null || range.base == null || range.high == null) return '확인되지 않음'
  return `${formatWon(range.low)} ~ ${formatWon(range.high)} (기준 ${formatWon(range.base)})`
}

function candidateSource(candidate: ResultCandidate) {
  return candidate.case_type === 'INDEPENDENT'
    ? `INDEPENDENT:${candidate.independent_model?.model_id ?? ''}`
    : `FRANCHISE:${candidate.franchise?.brand_id ?? ''}`
}

interface CapitalDecision {
  ownFunds: number | null
  minimumRequired: number | null
  minimumGap: number | null
  baseGap: number | null
  needsPlanChange: boolean
}

function capitalDecision(project: Project, candidate: ResultCandidate): CapitalDecision {
  const rawOwnFunds = project.state?.founder.own_funds_krw
  const ownFunds = typeof rawOwnFunds === 'number' && Number.isFinite(rawOwnFunds) ? rawOwnFunds : null
  const minimumRequired = candidate.financial_summary.initial_cash.low
  const baseRequired = candidate.financial_summary.initial_cash.base
  const minimumGap = ownFunds != null && minimumRequired != null ? Math.max(0, minimumRequired - ownFunds) : null
  const baseGap = ownFunds != null && baseRequired != null ? Math.max(0, baseRequired - ownFunds) : null
  return {
    ownFunds,
    minimumRequired,
    minimumGap,
    baseGap,
    needsPlanChange: minimumGap != null && minimumGap > 0,
  }
}

function resultStatus(project: Project, candidate: ResultCandidate) {
  if (capitalDecision(project, candidate).needsPlanChange) return '지금 예산에는 조금 큰 안이에요'
  return statusLabel(candidate.review_status)
}

function marketSignalLabel(signal: NonNullable<ResultCandidate['market_signals']>[number]) {
  return {
    CAFE_COUNT: '카페 업종 점포',
    OPEN_COUNT: '분기 신규 신고',
    CLOSE_COUNT: '분기 폐업 신고',
    CLOSURE_RATE: '분기 폐업 변화율',
    ESTIMATED_SALES: '분기 상권 추정매출',
    FOOT_TRAFFIC: '분기 추정 유동인구',
    RESIDENT_POPULATION: '거주인구',
    WORKER_POPULATION: '직장인구',
  }[signal.signal_type]
}

function marketSignalValue(signal: NonNullable<ResultCandidate['market_signals']>[number]) {
  if (signal.signal_type === 'ESTIMATED_SALES') return formatWon(signal.value)
  if (signal.signal_type === 'CLOSURE_RATE') return `${signal.value.toLocaleString('ko-KR')}%`
  if (signal.signal_type === 'FOOT_TRAFFIC') return `${signal.value.toLocaleString('ko-KR')}명·회`
  if (signal.signal_type === 'RESIDENT_POPULATION' || signal.signal_type === 'WORKER_POPULATION') return `${signal.value.toLocaleString('ko-KR')}명`
  return `${signal.value.toLocaleString('ko-KR')}개`
}

function formatDataDate(value: string | null) {
  if (!value) return '기준일 확인 필요'
  return new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeZone: 'Asia/Seoul' }).format(new Date(`${value}T00:00:00+09:00`))
}

function isHttpSource(value: string) {
  return /^https?:\/\//i.test(value)
}

function statusLabel(status: ResultCandidate['review_status']) {
  if (status === 'REVIEW_RECOMMENDED') return '검토 추천'
  if (status === 'CONDITIONAL_REVIEW') return '조건부 검토'
  return '제외'
}

function statusTone(status: ResultCandidate['review_status']) {
  return status === 'REVIEW_RECOMMENDED' ? 'success' : status === 'CONDITIONAL_REVIEW' ? 'warning' : ''
}

function ResultNav({ activePanel, onChange }: { activePanel: PanelName; onChange: (panel: PanelName) => void }) {
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    let nextIndex = index
    if (event.key === 'ArrowDown') nextIndex = (index + 1) % panels.length
    if (event.key === 'ArrowUp') nextIndex = (index - 1 + panels.length) % panels.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = panels.length - 1
    onChange(panels[nextIndex].id)
    window.setTimeout(() => document.getElementById(`tab-${panels[nextIndex].id}`)?.focus(), 0)
  }

  return <nav className="result-nav" role="tablist" aria-orientation="vertical" aria-label="결과 상세 항목">
    <p className="rail__caption">결과 상세</p>
    {panels.map((panel, index) => <button className="tab-button" role="tab" id={`tab-${panel.id}`} aria-controls={`panel-${panel.id}`} aria-selected={activePanel === panel.id} tabIndex={activePanel === panel.id ? 0 : -1} key={panel.id} onClick={() => onChange(panel.id)} onKeyDown={(event) => onKeyDown(event, index)}>{panel.label}</button>)}
  </nav>
}

function OverviewPanel({ project, candidate }: { project: Project; candidate: ResultCandidate }) {
  const capital = capitalDecision(project, candidate)
  return <><header className="panel__header"><h2>{capital.needsPlanChange ? '지금 예산에서 가능한 방법부터 찾아볼게요' : '지금 조건에서 살펴볼 만한 안이에요'}</h2><p>카페 창업, 감이 아닌 데이터로 시작하세요. 현재 자금과 필요한 비용을 먼저 비교했습니다.</p></header><div className="section-stack">
    <div className="judgement"><div className="judgement__status"><Badge tone={capital.needsPlanChange ? 'warning' : statusTone(candidate.review_status)}>{resultStatus(project, candidate)}</Badge><strong>{candidate.display_name}</strong><p>{capital.needsPlanChange && capital.minimumGap != null ? `최소 ${formatWon(capital.minimumGap)}을 더 마련하거나, 카페 규모와 비용을 줄여야 해요.` : displayText(candidate.summary)}</p></div><div className="judgement__aside"><strong>가장 먼저 볼 숫자</strong>{capital.ownFunds != null && <span>현재 자기자금 {formatWon(capital.ownFunds)}</span>}{capital.minimumRequired != null && <span>최소 필요자금 {formatWon(capital.minimumRequired)}</span>}{capital.minimumGap != null && capital.minimumGap > 0 && <span>최소 부족액 {formatWon(capital.minimumGap)}</span>}</div></div>
    {capital.needsPlanChange && <div className="decision-note" role="note"><strong>카페 창업 전체가 어렵다는 뜻은 아니에요.</strong><p>현재 선택된 {candidate.display_name}을 자기자금만으로 시작하기 어렵다는 뜻입니다. 더 작은 운영안이나 실제 점포 비용으로 다시 비교할 수 있어요.</p></div>}
    <article className="surface"><div className="surface__head"><h3>이번 계산에 사용한 정보</h3><p>공식 자료와 아직 확인이 필요한 기본 가정을 나누어 보여드려요.</p></div><dl className="summary-grid"><div className="summary-item"><dt>유형</dt><dd>{candidate.case_type === 'FRANCHISE' ? '프랜차이즈' : '개인카페'}</dd></div><div className="summary-item"><dt>초기 필요자금</dt><dd>{formatRange(candidate.financial_summary.initial_cash)}</dd><small>현재는 기본 운영 모델을 포함한 참고 범위입니다.</small></div><div className="summary-item"><dt>연결된 자료</dt><dd>{candidate.evidence_refs.length}건</dd><small>상권과 후보 판단에 연결된 자료입니다.</small></div><div className="summary-item"><dt>확인 전 기본 가정</dt><dd>{candidate.assumption_refs?.length ?? 0}건</dd><small>실제 점포와 견적이 들어오면 교체됩니다.</small></div></dl></article>
    <article className="surface"><div className="surface__head"><h3>정확도를 높이려면</h3></div>{candidate.missing_fields.length ? <ul className="plain-list">{candidate.missing_fields.map((item) => <li key={item.field}><div><strong>{internalLabel(item.field, '추가 확인 항목')}</strong><p>{displayText(item.impact)} 다음 확인: {displayText(item.next_check)}</p></div></li>)}</ul> : <p>계산에 필요한 기본값은 채워졌어요. 다만 실제 점포의 보증금·월세·권리금과 공사·장비 견적을 확인해야 최종 비용에 가까워집니다.</p>}</article>
  </div></>
}

function MarketPanel({ project, candidate }: { project: Project; candidate: ResultCandidate }) {
  const area = project.state?.area
  const signals = candidate.market_signals ?? []
  return <><header className="panel__header"><h2>이 동네에서 확인한 상권 정보예요</h2><p>확인된 자료만 보여드리며, 동네 전체 수치를 내 점포의 예상매출로 바꾸지 않습니다.</p></header><div className="section-stack"><article className="surface"><div className="surface__head"><h3>{area?.display_name ?? '희망 지역 확인 중'}</h3><p>현재 결과에 실제로 연결된 상권 자료를 기준으로 살펴봅니다.</p></div><table className="data-table"><tbody><tr><th>지역 확인</th><td>{internalLabel(area?.resolution_status ?? 'UNRESOLVED')}</td></tr><tr><th>확인한 상권 지표</th><td>{signals.length}개</td></tr><tr><th>후보 판단 연결 자료</th><td>{candidate.evidence_refs.length}건</td></tr><tr><th>실제 점포 자료</th><td>아직 없음</td></tr></tbody></table></article><article className="surface"><div className="surface__head"><h3>확인된 상권 수치</h3><p>후보 판단에 실제로 연결된 자료만 표시합니다.</p></div>{signals.length ? <ul className="market-signals">{signals.map((signal) => <li key={signal.evidence_id}><div className="market-signal__value"><span>{marketSignalLabel(signal)}</span><strong>{marketSignalValue(signal)}</strong></div><p>{signal.caveat}</p><div className="market-signal__source"><Badge tone={signal.freshness_status === 'FRESH' ? 'success' : 'warning'}>{signal.freshness_status === 'FRESH' ? '최신 기준 충족' : '기준일 확인 필요'}</Badge><span>{formatDataDate(signal.data_date)}</span>{isHttpSource(signal.source_ref) ? <a href={signal.source_ref} target="_blank" rel="noreferrer">공식 원문 보기</a> : <span>출처: {displayText(signal.source_title)}</span>}</div></li>)}</ul> : <p>현재 후보에 연결된 상권 수치가 없습니다. 확인되지 않은 값은 추정해서 채우지 않습니다.</p>}<p className="table-note">상권 평균이나 추정치는 개별 점포의 실제 매출이 아닙니다. 실제 점포를 정하면 임대 조건과 위치를 함께 다시 확인해야 해요.</p></article></div></>
}

function FranchisePanel({ candidate }: { candidate: ResultCandidate }) {
  if (candidate.case_type !== 'FRANCHISE' || !candidate.franchise) return <><header className="panel__header"><h2>개인카페 모델</h2><p>이 후보에는 프랜차이즈 조건이 적용되지 않습니다.</p></header><article className="surface"><p>운영 모델: {candidate.display_name}</p><p>조정된 조건: {candidate.independent_model?.adjusted_fields.length ? uniqueLabels(candidate.independent_model.adjusted_fields, '창업 조건').join(' · ') : '없음'}</p></article></>
  return <><header className="panel__header"><h2>가맹 조건을 문서 기준으로 확인합니다</h2><p>브랜드 존재 여부와 출점 가능 여부를 분리해서 보여줍니다.</p></header><article className="surface"><table className="data-table"><tbody><tr><th>브랜드</th><td>{candidate.display_name}</td></tr><tr><th>개인 가맹 여부</th><td><Badge tone={candidate.franchise.eligibility === 'VERIFIED' ? 'success' : 'warning'}>{internalLabel(candidate.franchise.eligibility)}</Badge></td></tr><tr><th>희망 지역 출점</th><td><Badge tone={candidate.franchise.availability_status === 'AVAILABLE' ? 'success' : 'warning'}>{internalLabel(candidate.franchise.availability_status)}</Badge></td></tr><tr><th>가맹 확인 근거</th><td>{candidate.franchise.eligibility_evidence_refs.length}건</td></tr><tr><th>정보공개서 근거</th><td>{candidate.franchise.disclosure_evidence_refs.length}건</td></tr></tbody></table></article></>
}

function FundsPanel({ project, candidate }: { project: Project; candidate: ResultCandidate }) {
  const finance = candidate.financial_summary
  const capital = capitalDecision(project, candidate)
  return <><header className="panel__header"><h2>내 자금과 필요한 비용을 함께 볼게요</h2><p>기본 모델로 계산한 범위와 실제로 더 확인할 비용을 나누어 보여드립니다.</p></header><div className="section-stack"><div className="fund-total"><span>예상 초기 필요자금</span><strong>{formatRange(finance.initial_cash)}</strong><small>실제 점포나 견적이 아닌 기본 운영 모델의 참고 범위입니다.</small></div>{capital.ownFunds != null && <article className="surface"><dl className="cost-list"><div className="cost-row"><dt>현재 자기자금</dt><dd>{formatWon(capital.ownFunds)}</dd></div><div className="cost-row"><dt>최소 필요자금과 차이</dt><dd>{capital.minimumGap != null && capital.minimumGap > 0 ? `${formatWon(capital.minimumGap)} 부족` : '최소 범위 충당 가능'}</dd></div>{capital.baseGap != null && capital.baseGap > 0 && <div className="cost-row"><dt>기준 필요자금과 차이</dt><dd>{formatWon(capital.baseGap)} 부족</dd></div>}</dl></article>}<article className="surface"><dl className="cost-list"><div className="cost-row"><dt>월 고정비</dt><dd>{formatRange(finance.monthly_fixed_cost)}</dd></div><div className="cost-row"><dt>월 손익분기 매출</dt><dd>{formatWon(finance.break_even_monthly_sales_krw)}</dd></div><div className="cost-row"><dt>하루 필요한 주문</dt><dd>{finance.required_daily_orders == null ? '확인되지 않음' : `${finance.required_daily_orders.toLocaleString('ko-KR')}건`}</dd></div></dl><p className="table-note">손익분기 매출과 주문 수는 계산값이며, 이 동네에서 실제로 달성할 수 있다는 뜻은 아닙니다.</p></article><article className="surface surface--flat"><div className="surface__head"><h3>실제 자료로 바꿔야 할 값</h3></div>{finance.unknown_cost_fields.length ? <ul className="plain-list plain-list--neutral">{uniqueLabels(finance.unknown_cost_fields, '추가 비용 항목').map((field) => <li key={field}>{field}</li>)}</ul> : <p>기본 계산 항목은 모두 채워졌어요. 다음 단계에서는 보증금·월세·권리금과 공사·장비 견적을 실제 자료로 바꿔야 합니다.</p>}</article></div></>
}

function RisksPanel({ candidate }: { candidate: ResultCandidate }) {
  const groupedRisks = Object.values(candidate.risks.reduce<Record<string, { severity: ResultCandidate['risks'][number]['severity']; summary: string; count: number }>>((groups, risk) => {
    const summary = displayText(risk.summary)
    const key = `${risk.severity}:${summary}`
    groups[key] = groups[key] ? { ...groups[key], count: groups[key].count + 1 } : { severity: risk.severity, summary, count: 1 }
    return groups
  }, {}))
  return <><header className="panel__header"><h2>판단을 뒤집을 조건부터 확인합니다</h2><p>위험, 반대 조건, 다음 행동을 사용자 관점에서 정리합니다.</p></header><div className="section-stack"><div className="warning-box" role="note"><span aria-hidden="true">!</span><p><strong>이 결과는 최종 창업 결정을 대신하지 않습니다.</strong> 계약, 송금, 대출과 최종 창업 여부는 사용자가 결정합니다.</p></div><article className="surface"><div className="surface__head"><h3>주요 위험</h3></div>{groupedRisks.length ? <ul className="plain-list">{groupedRisks.map((risk) => <li key={`${risk.severity}-${risk.summary}`}><div><strong>{severityLabel(risk.severity)}{risk.count > 1 ? ` · ${risk.count}개 항목` : ''}</strong><p>{risk.summary}</p></div></li>)}</ul> : <p>등록된 위험이 없습니다.</p>}</article><article className="surface"><div className="surface__head"><h3>판단 전환 조건</h3></div>{candidate.counterfactuals.length ? <ul className="plain-list plain-list--neutral">{candidate.counterfactuals.map((item) => <li key={`${item.variable}-${item.condition}`}><div><strong>{internalLabel(item.variable, '확인할 조건')}: {displayText(item.condition)}</strong><p>{displayText(item.decision_impact)}</p></div></li>)}</ul> : <p>등록된 판단 전환 조건이 없습니다.</p>}</article><article className="surface"><div className="surface__head"><h3>다음 검증 행동</h3></div><ol className="condition-list">{candidate.next_actions.map((action) => <li key={action}><div><strong>{displayText(action)}</strong></div></li>)}</ol></article></div></>
}

function FeedbackPanel({ client, projectId, onResult, suggestion }: { client: ControlApiClient; projectId: string; onResult: (result: ResultView) => void; suggestion?: string }) {
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState<FeedbackPreview | null>(null)
  const [status, setStatus] = useState('바꾸고 싶은 조건을 입력해 주세요.')
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!suggestion || preview || busy) return
    const timer = window.setTimeout(() => {
      setDraft(suggestion)
      inputRef.current?.focus()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [suggestion, preview, busy])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!draft.trim()) { setStatus('피드백이 비어 있습니다.'); inputRef.current?.focus(); return }
    setBusy(true)
    try {
      const next = await client.createFeedbackPreview(projectId, draft.trim())
      setPreview(next)
      setStatus(next.status === 'CLARIFICATION_REQUIRED' ? '추가 설명이 필요합니다.' : next.status === 'REVIEW_REQUIRED' ? '적용 전 변경안을 확인해 주세요.' : `변경안 상태: ${internalLabel(next.status)}`)
    } catch (error) { setStatus(userError(error, '변경안을 만들지 못했습니다.')) } finally { setBusy(false) }
  }

  const cancel = async () => {
    if (!preview) return
    setBusy(true)
    try { await client.cancelFeedback(projectId, preview.preview_id); setPreview(null); setStatus('변경안을 취소했습니다. 현재 결과는 바뀌지 않았습니다.') } catch (error) { setStatus(userError(error, '변경안을 취소하지 못했습니다.')) } finally { setBusy(false) }
  }

  const confirm = async () => {
    if (!preview?.proposal_digest) return
    setBusy(true)
    try {
      const resolution = await client.confirmFeedback(projectId, preview)
      if (resolution.workflow) {
        const progress = await waitForWorkflow(client, projectId, resolution.workflow, (next) => setStatus(`결과 재계산 ${next.completed_stage_count}/${next.total_stage_count}`))
        if (!['SUCCEEDED', 'PARTIAL'].includes(progress.status)) throw new Error(`재계산이 완료되지 않았습니다: ${internalLabel(progress.status)}`)
        onResult(await client.getResult(projectId))
      }
      setDraft(''); setPreview(null); setStatus('확인한 변경안을 반영하고 결과를 갱신했습니다.')
    } catch (error) { setStatus(userError(error, '변경안을 반영하지 못했습니다.')) } finally { setBusy(false) }
  }

  const changedFields = preview ? Object.keys(preview.after_founder ?? {}).filter((key) => JSON.stringify(preview.before_founder[key]) !== JSON.stringify(preview.after_founder?.[key])) : []
  return <section className="feedback-panel" id="resultFeedback" aria-labelledby="feedbackTitle"><div className="feedback-panel__head"><Badge tone="accent">결과 생성 후 사용</Badge><h2 id="feedbackTitle">결과 피드백</h2><p>확인 전에는 권위 상태와 결과가 바뀌지 않습니다.</p></div><form className="feedback-form" onSubmit={submit}><div className="field"><label htmlFor="feedbackInput">자연어 피드백</label><div className="feedback-compose"><textarea id="feedbackInput" ref={inputRef} value={draft} disabled={busy || preview !== null} onChange={(event) => setDraft(event.target.value)} placeholder="예: 저가 브랜드는 빼고 10평 이하로 보고 싶어" /><button className="btn btn--primary" disabled={busy || preview !== null} type="submit">{busy ? '처리 중' : '제안 만들기'}</button></div></div></form>{preview && <section className="proposal" aria-labelledby="proposalTitle"><div className="proposal__head"><h3 id="proposalTitle">적용 전 변경 확인</h3><p>상태: {internalLabel(preview.status)} · 아직 결과에는 반영되지 않았습니다.</p></div>{changedFields.map((field) => <div className="diff-row" key={field}><span className="diff-label">{internalLabel(field, '창업 조건')}</span><div className="diff-values"><span className="diff-old">{displayValue(preview.before_founder[field])}</span><span>→</span><span className="diff-new">{displayValue(preview.after_founder?.[field])}</span></div></div>)}{preview.clarifying_questions.map((question) => <p key={question}>{displayText(question)}</p>)}{preview.risk_flags.length > 0 && <p>주의: {uniqueLabels(preview.risk_flags).join(' · ')}</p>}<div className="feedback-actions"><button className="btn" disabled={busy} type="button" onClick={cancel}>제안 취소</button><button className="btn btn--primary" disabled={busy || preview.status !== 'REVIEW_REQUIRED' || !preview.proposal_digest} type="button" onClick={confirm}>{busy ? '변경 적용 중' : '변경 적용'}</button></div></section>}<p className="feedback-status" aria-live="polite">{status}</p><p className="feedback-note">계약, 결제, 본사 연락은 자동으로 실행하지 않습니다.</p></section>
}

function ActivePanel({ panel, project, candidate }: { panel: PanelName; project: Project; candidate: ResultCandidate }) {
  const content = { overview: <OverviewPanel project={project} candidate={candidate} />, market: <MarketPanel project={project} candidate={candidate} />, franchise: <FranchisePanel candidate={candidate} />, funds: <FundsPanel project={project} candidate={candidate} />, risks: <RisksPanel candidate={candidate} /> }[panel]
  return <section className="panel" id={`panel-${panel}`} role="tabpanel" aria-labelledby={`tab-${panel}`} tabIndex={0}>{content}</section>
}

interface PropertyRecalculation {
  application: PropertyTermsApplication
  candidate: ResultCandidate
}

const demoPropertyTerms = {
  address: '서울 마포구 공덕동 데모 점포 · 실매물 아님',
  area_sqm: '33',
  deposit_manwon: '3000',
  monthly_rent_manwon: '220',
  management_fee_manwon: '20',
  key_money_manwon: '1000',
}

function PreparationScreen({ candidate, selection, guide, busy, error, onRetry, onBack, onApply }: { candidate: ResultCandidate; selection: CandidateSelection; guide: PreparationGuide | null; busy: boolean; error: string; onRetry: () => void; onBack: () => void; onApply: (terms: PropertyTermsInput) => Promise<PropertyRecalculation> }) {
  const jurisdiction = guide?.jurisdiction_display_name
  const [values, setValues] = useState(demoPropertyTerms)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('데모 예시를 불러오거나 점포 조건을 직접 입력해 주세요.')
  const [outcome, setOutcome] = useState<PropertyRecalculation | null>(null)
  const setValue = (key: keyof typeof demoPropertyTerms, value: string) => setValues((current) => ({ ...current, [key]: value }))
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true); setStatus('입력한 점포 조건으로 비용을 다시 계산하고 있어요.')
    try {
      const next = await onApply({
        address: values.address.trim(), area_sqm: Number(values.area_sqm), floor: null,
        deposit_krw: Number(values.deposit_manwon) * 10_000,
        monthly_rent_krw: Number(values.monthly_rent_manwon) * 10_000,
        management_fee_krw: Number(values.management_fee_manwon) * 10_000,
        key_money_krw: values.key_money_manwon === '' ? null : Number(values.key_money_manwon) * 10_000,
      })
      setOutcome(next); setStatus('점포 조건을 반영해 비용을 다시 계산했습니다.')
    } catch (reason) { setStatus(userError(reason, '점포 조건을 반영하지 못했습니다.')) } finally { setSaving(false) }
  }
  const availableProcedures = guide?.procedures.filter((procedure) => procedure.steps.length > 0) ?? []
  return <div className="shell min-h-dvh"><header className="topbar"><a className="wordmark" href="#preparationTop">CaffeMate</a><div className="topbar__meta"><Badge tone="success">검토 대상 선택됨</Badge></div></header><main className="page preparation-page" id="preparationTop"><header className="preparation-hero"><div><p className="candidate-picker__count">선택한 창업안</p><h1>{candidate.display_name}에 점포 조건을 넣어보세요</h1><p>임시 범위로 계산한 창업비를 실제로 알아본 보증금·월세·관리비·권리금으로 바꿔 비교해요.</p></div><button className="btn btn--accent" type="button" onClick={onBack}>결과 비교로 돌아가기</button></header><div className="preparation-layout"><section className="preparation-main"><article className="surface" aria-labelledby="propertyTermsTitle"><div className="surface__head property-form__head"><div><h2 id="propertyTermsTitle">점포 조건 입력</h2><p>아직 점포가 없다면 데모 예시로 재계산 흐름을 먼저 확인할 수 있어요.</p></div><button className="btn btn--accent" type="button" disabled={saving} onClick={() => { setValues(demoPropertyTerms); setOutcome(null); setStatus('데모 입력 예시를 불러왔습니다. 값을 자유롭게 바꿔보세요.') }}>데모 입력 예시 불러오기</button></div><p className="demo-input-note"><strong>데모 입력 예시</strong>는 입력 형식을 보여주기 위한 값이며 실매물·공식 근거가 아닙니다.</p><form className="property-form" onSubmit={submit}><label className="field"><span>점포 주소</span><input required value={values.address} onChange={(event) => setValue('address', event.target.value)} /></label><label className="field"><span>면적(㎡)</span><input required min="1" step="0.1" type="number" value={values.area_sqm} onChange={(event) => setValue('area_sqm', event.target.value)} /></label><label className="field"><span>보증금(만원)</span><input required min="0" type="number" value={values.deposit_manwon} onChange={(event) => setValue('deposit_manwon', event.target.value)} /></label><label className="field"><span>월세(만원)</span><input required min="0" type="number" value={values.monthly_rent_manwon} onChange={(event) => setValue('monthly_rent_manwon', event.target.value)} /></label><label className="field"><span>관리비(만원)</span><input required min="0" type="number" value={values.management_fee_manwon} onChange={(event) => setValue('management_fee_manwon', event.target.value)} /></label><label className="field"><span>권리금(만원)</span><input min="0" type="number" value={values.key_money_manwon} onChange={(event) => setValue('key_money_manwon', event.target.value)} /></label><div className="property-form__action"><button className="btn btn--primary" disabled={saving || !selection.property_intake_enabled} type="submit">{saving ? '재계산 중' : '이 조건으로 비용 다시 계산'}</button><p aria-live="polite">{status}</p></div></form>{outcome && <section className="property-comparison" aria-labelledby="propertyComparisonTitle"><h3 id="propertyComparisonTitle">임시값과 점포 반영값 비교</h3><div className="diff-row"><span className="diff-label">초기 필요자금 기준</span><div className="diff-values"><span className="diff-old">{formatWon(outcome.application.previous_financial_summary.initial_cash.base)}</span><span>→</span><strong className="diff-new">{formatWon(outcome.candidate.financial_summary.initial_cash.base)}</strong></div></div><div className="diff-row"><span className="diff-label">월 고정비 기준</span><div className="diff-values"><span className="diff-old">{formatWon(outcome.application.previous_financial_summary.monthly_fixed_cost.base)}</span><span>→</span><strong className="diff-new">{formatWon(outcome.candidate.financial_summary.monthly_fixed_cost.base)}</strong></div></div><p>입력한 보증금·월세·관리비·권리금만 확정값으로 교체했습니다. 공사·장비 등 나머지는 기존 참고 범위를 유지합니다.</p></section>}</article><article className="surface" aria-labelledby="evidenceChecklistTitle"><div className="surface__head"><h2 id="evidenceChecklistTitle">다음에 확인할 자료</h2><p>점포 비용을 반영한 뒤 견적과 계약 조건을 순서대로 확인하면 돼요.</p></div>{selection.required_evidence.length ? <ol className="preparation-checklist">{selection.required_evidence.map((item, index) => <li key={item.code}><span className="preparation-checklist__number">{index + 1}</span><div><strong>{item.title}</strong><p>{item.reason}</p></div></li>)}</ol> : <p>현재 별도로 지정된 필수 자료는 없어요.</p>}</article></section><aside className="preparation-side"><article className="surface"><div className="surface__head"><h2>공식 창업 절차</h2><p>{jurisdiction ? `${jurisdiction} 기준` : '선택 지역 기준'} 안내는 보조 정보이며, 실제 신청은 자동으로 진행하지 않아요.</p></div>{busy && <div className="preparation-loading" role="status"><span aria-hidden="true" /><p>공식 절차를 확인하고 있어요.</p></div>}{!busy && availableProcedures.length > 0 && <ul className="plain-list">{availableProcedures.flatMap((procedure) => procedure.steps).map((step) => <li key={`${step.procedure_type}-${step.step_order}`}><div><strong>{step.title}</strong><p>{step.authority}</p></div></li>)}</ul>}{!busy && availableProcedures.length === 0 && <div className="procedure-unavailable"><strong>공식 절차 자료 연결 전</strong><p>현재 데모에서는 점포 비용 재계산을 먼저 제공합니다.</p>{error && <button className="btn btn--accent" type="button" onClick={onRetry}>다시 확인</button>}</div>}</article><article className="decision-note"><strong>지금 하는 일</strong><span>계약 전 점포 조건을 넣어, 선택한 안이 예산에 가까워지는지 확인합니다.</span></article></aside></div></main><footer className="footer"><strong>CaffeMate</strong><span>카페 창업, 감이 아닌 데이터로 시작하세요.</span></footer></div>
}

function ResultScreen({ client, project, initialResult }: { client: ControlApiClient; project: Project; initialResult: ResultView }) {
  const [result, setResult] = useState(initialResult)
  const [activePanel, setActivePanel] = useState<PanelName>('overview')
  const [activeCandidateIndex, setActiveCandidateIndex] = useState(0)
  const [selection, setSelection] = useState<CandidateSelection | null>(null)
  const [actionStatus, setActionStatus] = useState('결과를 확인하고 지금 상황에 맞는 다음 단계를 선택해 주세요.')
  const [selectionBusy, setSelectionBusy] = useState(false)
  const [preparationOpen, setPreparationOpen] = useState(false)
  const [preparationGuide, setPreparationGuide] = useState<PreparationGuide | null>(null)
  const [preparationBusy, setPreparationBusy] = useState(false)
  const [preparationError, setPreparationError] = useState('')
  const [feedbackSuggestion, setFeedbackSuggestion] = useState('')
  const candidates = result.candidates
  const activeCandidate = candidates[activeCandidateIndex] ?? candidates[0]
  const capital = activeCandidate ? capitalDecision(project, activeCandidate) : null
  const createdAt = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(result.created_at))

  const loadPreparationGuide = async (nextSelection: CandidateSelection) => {
    setPreparationBusy(true); setPreparationError('')
    try { setPreparationGuide(await client.getPreparationGuide(project.project_id, nextSelection.selection_id)) } catch (error) { setPreparationError(userError(error, '공식 절차를 불러오지 못했습니다. 잠시 뒤 다시 확인해 주세요.')) } finally { setPreparationBusy(false) }
  }

  const select = async () => {
    setSelectionBusy(true)
    try { const next = await client.selectCandidate(project.project_id, result, activeCandidate.candidate_id); setSelection(next); setPreparationOpen(true); setPreparationGuide(null); setActionStatus(`${activeCandidate.display_name}을 검토 대상으로 선택했습니다.`); window.scrollTo({ top: 0 }); void loadPreparationGuide(next) } catch (error) { setActionStatus(userError(error, '후보를 선택하지 못했습니다.')) } finally { setSelectionBusy(false) }
  }
  const applyPropertyTerms = async (terms: PropertyTermsInput): Promise<PropertyRecalculation> => {
    if (!selection) throw new Error('선택한 창업안이 없습니다.')
    const application = await client.applyPropertyTerms(project.project_id, selection.selection_id, selection.selected_state_version, terms)
    const terminal = await waitForWorkflow(client, project.project_id, application.recompute_workflow, (progress) => setActionStatus(`점포 조건 재계산 ${progress.completed_stage_count}/${progress.total_stage_count}`))
    if (!['SUCCEEDED', 'PARTIAL'].includes(terminal.status)) throw new Error('점포 조건 재계산을 완료하지 못했습니다.')
    const nextResult = await client.getResult(project.project_id)
    const source = candidateSource(activeCandidate)
    const nextIndex = nextResult.candidates.findIndex((nextCandidate) => candidateSource(nextCandidate) === source)
    const nextCandidate = nextResult.candidates[nextIndex]
    if (!nextCandidate) throw new Error('재계산된 후보를 찾지 못했습니다.')
    setResult(nextResult); setActiveCandidateIndex(nextIndex); setActionStatus('점포 조건을 반영해 비용을 다시 계산했습니다.')
    return { application, candidate: nextCandidate }
  }
  const updateResult = (next: ResultView) => { setResult(next); setActiveCandidateIndex(0); setActivePanel('overview'); setSelection(null); setPreparationOpen(false); setPreparationGuide(null) }
  if (!activeCandidate) return <main className="analysis-stage"><h1>표시할 후보가 없습니다</h1><p>결과 계약을 다시 확인해 주세요.</p></main>

  if (selection && preparationOpen) return <PreparationScreen candidate={candidates.find((candidate) => candidate.candidate_id === selection.candidate_id) ?? activeCandidate} selection={selection} guide={preparationGuide} busy={preparationBusy} error={preparationError} onRetry={() => void loadPreparationGuide(selection)} onBack={() => { setPreparationOpen(false); window.scrollTo({ top: 0 }) }} onApply={applyPropertyTerms} />

  return <div className="shell min-h-dvh"><header className="topbar"><a className="wordmark" href="#top">CaffeMate</a><div className="topbar__meta"><Badge tone={result.freshness === 'CURRENT' ? 'success' : 'warning'}>{internalLabel(result.freshness)}</Badge><span className="version">결과 생성 {createdAt}</span></div></header><main className="page" id="top">{candidates.length > 1 && <section className="candidate-picker" aria-labelledby="candidatePickerTitle"><div className="candidate-picker__head"><div><p className="candidate-picker__count">검토 후보 {candidates.length}개</p><h2 id="candidatePickerTitle">추천안부터 살펴보세요</h2></div><p>모든 후보는 같은 결과 계약으로 비교합니다.</p></div><div className="candidate-tabs" role="tablist" aria-label="창업안 후보">{candidates.map((candidate, index) => <button id={`candidate-tab-${index}`} className="candidate-tab" type="button" role="tab" aria-selected={activeCandidateIndex === index} aria-controls="candidate-report" tabIndex={activeCandidateIndex === index ? 0 : -1} data-recommended={candidate.is_primary_next_review || undefined} key={candidate.candidate_id} onClick={() => { setActiveCandidateIndex(index); setActivePanel('overview') }}><span className="candidate-tab__number">{candidate.rank ? `${candidate.rank}순위` : '순위 없음'}</span><strong>{candidate.display_name}</strong><small>{resultStatus(project, candidate)} · {formatRange(candidate.financial_summary.initial_cash)}</small></button>)}</div></section>}<section className="intro" aria-labelledby="pageTitle"><div className="intro__copy"><div className="context-line"><Badge>{activeCandidate.case_type === 'FRANCHISE' ? '프랜차이즈' : '개인카페'}</Badge><Badge tone={capital?.needsPlanChange ? 'warning' : statusTone(activeCandidate.review_status)}>{resultStatus(project, activeCandidate)}</Badge></div><h1 id="pageTitle">{activeCandidate.display_name}</h1><p className="intro__lede">{capital?.needsPlanChange ? '지금 예산에 맞는 운영안이나 실제 점포 비용으로 한 번 더 비교해 보세요.' : displayText(activeCandidate.summary)}</p></div>{result.freshness === 'STALE' && <div className="demo-notice" role="note"><span className="demo-notice__mark">!</span><p>현재 결과는 이전 상태를 기준으로 생성되었습니다. 변경된 항목: {uniqueLabels(result.stale_head_dimensions, '결과 기준').join(' · ')}</p></div>}</section><div className="mobile-switcher"><label htmlFor="sectionSelect">결과 항목</label><select className="section-select" id="sectionSelect" value={activePanel} onChange={(event) => setActivePanel(event.target.value as PanelName)}>{panels.map((panel) => <option value={panel.id} key={panel.id}>{panel.label}</option>)}</select></div><div className="workbench"><aside className="rail"><div className="rail__inner"><FeedbackPanel client={client} projectId={project.project_id} onResult={updateResult} suggestion={feedbackSuggestion} /><ResultNav activePanel={activePanel} onChange={setActivePanel} /></div></aside><div className="panels" id="candidate-report"><ActivePanel panel={activePanel} project={project} candidate={activeCandidate} key={`${activeCandidate.candidate_id}-${activePanel}`} /><aside className="action-dock"><p className="action-dock__status" aria-live="polite">{actionStatus}</p><div className="action-group">{selection ? <><button className="btn btn--primary" type="button" onClick={() => { setPreparationOpen(true); window.scrollTo({ top: 0 }) }}>준비 자료 보기</button><button className="btn btn--accent" type="button" onClick={() => document.getElementById('feedbackInput')?.focus()}>조건 바꾸기</button></> : capital?.needsPlanChange ? <><button className="btn btn--primary" onClick={() => { setFeedbackSuggestion('현재 자기자금 범위에 더 가까운 작은 개인카페 운영안으로 다시 보고 싶어요.'); setActionStatus('피드백 내용을 준비했어요. 확인한 뒤 제안을 만들어 주세요.'); window.setTimeout(() => document.getElementById('feedbackInput')?.focus(), 0) }}>예산에 맞는 작은 안 보기</button><button className="btn btn--accent" disabled={selectionBusy} onClick={select}>{selectionBusy ? '선택 중' : '이 안을 계속 검토하기'}</button></> : <><button className="btn btn--primary" disabled={selectionBusy} onClick={select}>{selectionBusy ? '선택 중' : '이 안을 계속 검토하기'}</button><button className="btn btn--accent" onClick={() => document.getElementById('feedbackInput')?.focus()}>조건 바꾸기</button></>}</div>{selection && <p className="table-note">{candidates.find((candidate) => candidate.candidate_id === selection.candidate_id)?.display_name ?? '선택한 창업안'}의 준비 자료를 확인할 수 있어요.</p>}</aside></div></div></main><footer className="footer"><strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span><span>결과 기준 {result.current_head.state_version}번째 변경</span></footer></div>
}

export interface AppProps {
  authGateway?: AuthGateway
  apiFactory?: (session: AuthSession) => ControlApiClient
}

export default function App({ authGateway, apiFactory }: AppProps = {}) {
  const auth = useMemo(() => authGateway ?? createFirebaseAuthGateway(), [authGateway])
  const [screen, setScreen] = useState<AppScreen>('welcome')
  const [client, setClient] = useState<ControlApiClient | null>(null)
  const [project, setProject] = useState<Project | null>(null)
  const [result, setResult] = useState<ResultView | null>(null)
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState('')
  const [progress, setProgress] = useState<WorkflowProgress | null>(null)

  const start = async () => {
    setLoginBusy(true); setLoginError('')
    try {
      const session = await auth.signIn()
      const nextClient = apiFactory ? apiFactory(session) : createControlApiClient(session)
      const nextProject = await nextClient.createProject()
      setClient(nextClient); setProject(nextProject); setScreen('onboarding'); window.scrollTo({ top: 0 })
    } catch (error) { setLoginError(userError(error, 'Google 로그인에 실패했습니다.')) } finally { setLoginBusy(false) }
  }

  const completeOnboarding = async (values: OnboardingValues, areaSelectionToken: string) => {
    if (!client || !project) throw new Error('프로젝트 연결이 준비되지 않았습니다.')
    const confirmedProject = await client.confirmOnboarding(project.project_id, values, areaSelectionToken)
    setProject(confirmedProject)
    const workflow = await client.startFirstProposal(project.project_id)
    const terminal = await waitForWorkflow(client, project.project_id, workflow, setProgress)
    if (terminal.status === 'WAITING_FOR_HUMAN') return
    if (!['SUCCEEDED', 'PARTIAL'].includes(terminal.status)) {
      const reasons = uniqueLabels(terminal.terminal_reason_codes).join(' · ')
      throw new Error(`첫 분석이 완료되지 않았습니다: ${internalLabel(terminal.status)}${reasons ? ` (${reasons})` : ''}`)
    }
    const nextResult = await client.getResult(project.project_id)
    setResult(nextResult); setScreen('result'); window.scrollTo({ top: 0 })
  }

  if (screen === 'welcome') return <Welcome onStart={start} busy={loginBusy} error={loginError} />
  if (screen === 'onboarding') return <><Onboarding onComplete={completeOnboarding} searchAreas={async (query) => {
    if (!client || !project) throw new Error('프로젝트 연결이 준비되지 않았습니다.')
    return (await client.searchAreas(project.project_id, query)).candidates
  }} />{progress && <p className="workflow-progress" aria-live="polite">{progress.status === 'WAITING_FOR_HUMAN' ? `추가 확인 필요 · ${uniqueLabels(progress.human_review_requests.flatMap((request) => request.reason_codes)).join(' · ')}` : `분석 진행 ${progress.completed_stage_count}/${progress.total_stage_count} · ${progress.current_stage_codes.length ? uniqueLabels(progress.current_stage_codes).join(' · ') : internalLabel(progress.status)}`}</p>}</>
  if (client && project && result) return <ResultScreen client={client} project={project} initialResult={result} />
  return <main className="analysis-stage"><h1>결과를 불러오지 못했습니다</h1><p>프로젝트 상태를 다시 확인해 주세요.</p></main>
}
