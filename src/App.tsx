import { type FormEvent, type KeyboardEvent, useMemo, useRef, useState } from 'react'
import Onboarding from './Onboarding'
import Welcome from './Welcome'
import { createFirebaseAuthGateway, type AuthGateway, type AuthSession } from './auth'
import {
  createControlApiClient,
  waitForWorkflow,
  type CandidateSelection,
  type ControlApiClient,
  type FeedbackPreview,
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

function OverviewPanel({ candidate }: { candidate: ResultCandidate }) {
  return <><header className="panel__header"><h2>먼저 결론부터 검토합니다</h2><p>점수 대신 현재 판단과 근거, 아직 확인되지 않은 정보를 함께 보여줍니다.</p></header><div className="section-stack">
    <div className="judgement"><div className="judgement__status"><Badge tone={statusTone(candidate.review_status)}>{statusLabel(candidate.review_status)}</Badge><strong>{candidate.display_name}</strong><p>{displayText(candidate.summary)}</p></div><div className="judgement__aside"><strong>판단 근거</strong>{uniqueLabels(candidate.reason_codes).map((reason) => <span key={reason}>{reason}</span>)}</div></div>
    <article className="surface"><div className="surface__head"><h3>창업안 요약</h3><p>검증 규칙을 통과한 현재 결과입니다.</p></div><dl className="summary-grid"><div className="summary-item"><dt>유형</dt><dd>{candidate.case_type === 'FRANCHISE' ? '프랜차이즈' : '개인카페'}</dd></div><div className="summary-item"><dt>초기 필요자금</dt><dd>{formatRange(candidate.financial_summary.initial_cash)}</dd><small>근거 {candidate.financial_summary.initial_cash.provenance_refs.length}건</small></div><div className="summary-item"><dt>확인된 근거</dt><dd>{candidate.evidence_refs.length}건</dd><small>{candidate.evidence_refs.length ? '확인된 자료만 계산과 판단에 사용했습니다.' : '확보된 근거가 없습니다.'}</small></div><div className="summary-item"><dt>검토 중인 가정</dt><dd>{candidate.assumption_refs?.length ?? 0}건</dd><small>확정 사실과 분리해 관리합니다.</small></div></dl></article>
    <article className="surface"><div className="surface__head"><h3>아직 확인할 정보</h3></div>{candidate.missing_fields.length ? <ul className="plain-list">{candidate.missing_fields.map((item) => <li key={item.field}><div><strong>{internalLabel(item.field, '추가 확인 항목')}</strong><p>{displayText(item.impact)} 다음 확인: {displayText(item.next_check)}</p></div></li>)}</ul> : <p>현재 결과에서 필수 누락 항목이 발견되지 않았습니다.</p>}</article>
  </div></>
}

function MarketPanel({ project, candidate }: { project: Project; candidate: ResultCandidate }) {
  const area = project.state?.area
  return <><header className="panel__header"><h2>상권 신호와 근거 상태</h2><p>확보되지 않은 상권 정보는 좋은 신호로 바꾸지 않습니다.</p></header><article className="surface"><div className="surface__head"><h3>{area?.display_name ?? '희망 지역 확인 중'}</h3><p>지역 확인 상태와 실제로 확보한 자료 범위를 표시합니다.</p></div><table className="data-table"><tbody><tr><th>지역 확인</th><td>{internalLabel(area?.resolution_status ?? 'UNRESOLVED')}</td></tr><tr><th>자료 범위</th><td>{internalLabel(area?.coverage_profile ?? 'UNKNOWN')}</td></tr><tr><th>지역 근거</th><td>{area?.evidence_ids.length ?? 0}건</td></tr><tr><th>후보 근거</th><td>{candidate.evidence_refs.length}건</td></tr><tr><th>아직 확보하지 못한 정보</th><td>{area?.unavailable_fields.length ? uniqueLabels(area.unavailable_fields, '추가 확인 항목').join(' · ') : '없음'}</td></tr></tbody></table><p className="table-note">상권 평균이나 추정치는 개별 점포의 실제 매출로 표시하지 않습니다.</p></article></>
}

function FranchisePanel({ candidate }: { candidate: ResultCandidate }) {
  if (candidate.case_type !== 'FRANCHISE' || !candidate.franchise) return <><header className="panel__header"><h2>개인카페 모델</h2><p>이 후보에는 프랜차이즈 조건이 적용되지 않습니다.</p></header><article className="surface"><p>운영 모델: {candidate.display_name}</p><p>조정된 조건: {candidate.independent_model?.adjusted_fields.length ? uniqueLabels(candidate.independent_model.adjusted_fields, '창업 조건').join(' · ') : '없음'}</p></article></>
  return <><header className="panel__header"><h2>가맹 조건을 문서 기준으로 확인합니다</h2><p>브랜드 존재 여부와 출점 가능 여부를 분리해서 보여줍니다.</p></header><article className="surface"><table className="data-table"><tbody><tr><th>브랜드</th><td>{candidate.display_name}</td></tr><tr><th>개인 가맹 여부</th><td><Badge tone={candidate.franchise.eligibility === 'VERIFIED' ? 'success' : 'warning'}>{internalLabel(candidate.franchise.eligibility)}</Badge></td></tr><tr><th>희망 지역 출점</th><td><Badge tone={candidate.franchise.availability_status === 'AVAILABLE' ? 'success' : 'warning'}>{internalLabel(candidate.franchise.availability_status)}</Badge></td></tr><tr><th>가맹 확인 근거</th><td>{candidate.franchise.eligibility_evidence_refs.length}건</td></tr><tr><th>정보공개서 근거</th><td>{candidate.franchise.disclosure_evidence_refs.length}건</td></tr></tbody></table></article></>
}

function FundsPanel({ candidate }: { candidate: ResultCandidate }) {
  const finance = candidate.financial_summary
  return <><header className="panel__header"><h2>필요자금은 누락 비용까지 봅니다</h2><p>확인된 범위와 아직 계산할 수 없는 비용을 분리합니다.</p></header><div className="section-stack"><div className="fund-total"><span>현재 계산된 초기 필요자금</span><strong>{formatRange(finance.initial_cash)}</strong><small>근거 {finance.initial_cash.provenance_refs.length}건</small></div><article className="surface"><dl className="cost-list"><div className="cost-row"><dt>월 고정비</dt><dd>{formatRange(finance.monthly_fixed_cost)}</dd></div><div className="cost-row"><dt>월 손익분기 매출</dt><dd>{formatWon(finance.break_even_monthly_sales_krw)}</dd></div><div className="cost-row"><dt>일 필요 주문</dt><dd>{finance.required_daily_orders == null ? '확인되지 않음' : `${finance.required_daily_orders.toLocaleString('ko-KR')}건`}</dd></div></dl></article><article className="surface surface--flat"><div className="surface__head"><h3>아직 빠진 비용</h3></div>{finance.unknown_cost_fields.length ? <ul className="plain-list plain-list--neutral">{uniqueLabels(finance.unknown_cost_fields, '추가 비용 항목').map((field) => <li key={field}>{field}</li>)}</ul> : <p>현재 계산에서 누락 비용이 발견되지 않았습니다.</p>}</article></div></>
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

function FeedbackPanel({ client, projectId, onResult }: { client: ControlApiClient; projectId: string; onResult: (result: ResultView) => void }) {
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState<FeedbackPreview | null>(null)
  const [status, setStatus] = useState('바꾸고 싶은 조건을 입력해 주세요.')
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)

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
  const content = { overview: <OverviewPanel candidate={candidate} />, market: <MarketPanel project={project} candidate={candidate} />, franchise: <FranchisePanel candidate={candidate} />, funds: <FundsPanel candidate={candidate} />, risks: <RisksPanel candidate={candidate} /> }[panel]
  return <section className="panel" id={`panel-${panel}`} role="tabpanel" aria-labelledby={`tab-${panel}`} tabIndex={0}>{content}</section>
}

function ResultScreen({ client, project, initialResult }: { client: ControlApiClient; project: Project; initialResult: ResultView }) {
  const [result, setResult] = useState(initialResult)
  const [activePanel, setActivePanel] = useState<PanelName>('overview')
  const [activeCandidateIndex, setActiveCandidateIndex] = useState(0)
  const [selection, setSelection] = useState<CandidateSelection | null>(null)
  const [actionStatus, setActionStatus] = useState('후보를 검토하고 다음 준비 대상으로 선택할 수 있습니다.')
  const [selectionBusy, setSelectionBusy] = useState(false)
  const candidates = result.candidates
  const activeCandidate = candidates[activeCandidateIndex] ?? candidates[0]
  const createdAt = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(result.created_at))

  const select = async () => {
    setSelectionBusy(true)
    try { const next = await client.selectCandidate(project.project_id, result, activeCandidate.candidate_id); setSelection(next); setActionStatus(`${activeCandidate.display_name}을 다음 준비 대상으로 선택했습니다.`) } catch (error) { setActionStatus(userError(error, '후보를 선택하지 못했습니다.')) } finally { setSelectionBusy(false) }
  }
  const updateResult = (next: ResultView) => { setResult(next); setActiveCandidateIndex(0); setActivePanel('overview'); setSelection(null) }
  if (!activeCandidate) return <main className="analysis-stage"><h1>표시할 후보가 없습니다</h1><p>결과 계약을 다시 확인해 주세요.</p></main>

  return <div className="shell min-h-dvh"><header className="topbar"><a className="wordmark" href="#top">CaffeMate</a><div className="topbar__meta"><Badge tone={result.freshness === 'CURRENT' ? 'success' : 'warning'}>{internalLabel(result.freshness)}</Badge><span className="version">결과 생성 {createdAt}</span></div></header><main className="page" id="top">{candidates.length > 1 && <section className="candidate-picker" aria-labelledby="candidatePickerTitle"><div className="candidate-picker__head"><div><p className="candidate-picker__count">검토 후보 {candidates.length}개</p><h2 id="candidatePickerTitle">추천안부터 살펴보세요</h2></div><p>모든 후보는 같은 결과 계약으로 비교합니다.</p></div><div className="candidate-tabs" role="tablist" aria-label="창업안 후보">{candidates.map((candidate, index) => <button id={`candidate-tab-${index}`} className="candidate-tab" type="button" role="tab" aria-selected={activeCandidateIndex === index} aria-controls="candidate-report" tabIndex={activeCandidateIndex === index ? 0 : -1} data-recommended={candidate.is_primary_next_review || undefined} key={candidate.candidate_id} onClick={() => { setActiveCandidateIndex(index); setActivePanel('overview') }}><span className="candidate-tab__number">{candidate.rank ? `${candidate.rank}순위` : '순위 없음'}</span><strong>{candidate.display_name}</strong><small>{statusLabel(candidate.review_status)} · {formatRange(candidate.financial_summary.initial_cash)}</small></button>)}</div></section>}<section className="intro" aria-labelledby="pageTitle"><div className="intro__copy"><div className="context-line"><Badge>{activeCandidate.case_type === 'FRANCHISE' ? '프랜차이즈' : '개인카페'}</Badge><Badge tone={statusTone(activeCandidate.review_status)}>{statusLabel(activeCandidate.review_status)}</Badge><Badge tone={result.audit_status === 'PASSED' ? 'success' : 'warning'}>감사 {internalLabel(result.audit_status)}</Badge></div><h1 id="pageTitle">{activeCandidate.display_name}</h1><p className="intro__lede">{displayText(activeCandidate.summary)}</p></div>{result.freshness === 'STALE' && <div className="demo-notice" role="note"><span className="demo-notice__mark">!</span><p>현재 결과는 이전 상태를 기준으로 생성되었습니다. 변경된 항목: {uniqueLabels(result.stale_head_dimensions, '결과 기준').join(' · ')}</p></div>}</section><div className="mobile-switcher"><label htmlFor="sectionSelect">결과 항목</label><select className="section-select" id="sectionSelect" value={activePanel} onChange={(event) => setActivePanel(event.target.value as PanelName)}>{panels.map((panel) => <option value={panel.id} key={panel.id}>{panel.label}</option>)}</select></div><div className="workbench"><aside className="rail"><div className="rail__inner"><FeedbackPanel client={client} projectId={project.project_id} onResult={updateResult} /><ResultNav activePanel={activePanel} onChange={setActivePanel} /></div></aside><div className="panels" id="candidate-report"><ActivePanel panel={activePanel} project={project} candidate={activeCandidate} key={`${activeCandidate.candidate_id}-${activePanel}`} /><aside className="action-dock"><p className="action-dock__status" aria-live="polite">{actionStatus}</p><div className="action-group"><button className="btn btn--primary" disabled={selectionBusy || selection?.candidate_id === activeCandidate.candidate_id} onClick={select}>{selectionBusy ? '선택 반영 중' : selection?.candidate_id === activeCandidate.candidate_id ? '선택 완료' : '대상 선택'}</button><button className="btn btn--accent" onClick={() => document.getElementById('feedbackInput')?.focus()}>피드백으로 이동</button></div>{selection && <p className="table-note">필요 자료 {selection.required_evidence.length}건 · 점포 입력 {selection.property_intake_enabled ? '가능' : '불가'} · 문서 입력 {selection.document_intake_enabled ? '가능' : '불가'}</p>}</aside></div></div></main><footer className="footer"><strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span><span>결과 기준 {result.current_head.state_version}번째 변경</span></footer></div>
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
