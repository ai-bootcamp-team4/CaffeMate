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
    <div className="judgement"><div className="judgement__status"><Badge tone={statusTone(candidate.review_status)}>{statusLabel(candidate.review_status)}</Badge><strong>{candidate.display_name}</strong><p>{candidate.summary}</p></div><div className="judgement__aside"><strong>판단 근거 코드</strong>{candidate.reason_codes.map((reason) => <span key={reason}>{reason}</span>)}</div></div>
    <article className="surface"><div className="surface__head"><h3>창업안 요약</h3><p>Control API가 검증한 현재 결과입니다.</p></div><dl className="summary-grid"><div className="summary-item"><dt>유형</dt><dd>{candidate.case_type === 'FRANCHISE' ? '프랜차이즈' : '개인카페'}</dd></div><div className="summary-item"><dt>초기 필요자금</dt><dd>{formatRange(candidate.financial_summary.initial_cash)}</dd><small>근거 {candidate.financial_summary.initial_cash.provenance_refs.length}건</small></div><div className="summary-item"><dt>근거</dt><dd>{candidate.evidence_refs.length}건</dd><small>{candidate.evidence_refs.length ? candidate.evidence_refs.join(' · ') : '확보된 근거가 없습니다.'}</small></div><div className="summary-item"><dt>가정</dt><dd>{candidate.assumption_refs?.length ?? 0}건</dd><small>확정 사실과 분리해 관리합니다.</small></div></dl></article>
    <article className="surface"><div className="surface__head"><h3>아직 확인할 정보</h3></div>{candidate.missing_fields.length ? <ul className="plain-list">{candidate.missing_fields.map((item) => <li key={item.field}><div><strong>{item.field}</strong><p>{item.impact} 다음 확인: {item.next_check}</p></div></li>)}</ul> : <p>현재 결과 계약에서 필수 누락 항목이 발견되지 않았습니다.</p>}</article>
  </div></>
}

function MarketPanel({ project, candidate }: { project: Project; candidate: ResultCandidate }) {
  const area = project.state?.area
  return <><header className="panel__header"><h2>상권 신호와 근거 상태</h2><p>확보되지 않은 상권 정보는 좋은 신호로 바꾸지 않습니다.</p></header><article className="surface"><div className="surface__head"><h3>{area?.display_name ?? '희망 지역 확인 중'}</h3><p>지역 해석 상태와 근거 범위를 표시합니다.</p></div><table className="data-table"><tbody><tr><th>지역 해석</th><td>{area?.resolution_status ?? 'UNRESOLVED'}</td></tr><tr><th>자료 범위</th><td>{area?.coverage_profile ?? '확인되지 않음'}</td></tr><tr><th>지역 근거</th><td>{area?.evidence_ids.length ?? 0}건</td></tr><tr><th>후보 근거</th><td>{candidate.evidence_refs.length}건</td></tr><tr><th>확보 불가 항목</th><td>{area?.unavailable_fields.length ? area.unavailable_fields.join(' · ') : '없음'}</td></tr></tbody></table><p className="table-note">상권 평균이나 추정치는 개별 점포의 실제 매출로 표시하지 않습니다.</p></article></>
}

function FranchisePanel({ candidate }: { candidate: ResultCandidate }) {
  if (candidate.case_type !== 'FRANCHISE' || !candidate.franchise) return <><header className="panel__header"><h2>개인카페 모델</h2><p>이 후보에는 프랜차이즈 조건이 적용되지 않습니다.</p></header><article className="surface"><p>모델 ID: {candidate.independent_model?.model_id ?? '확인되지 않음'}</p><p>조정 항목: {candidate.independent_model?.adjusted_fields.join(' · ') || '없음'}</p></article></>
  return <><header className="panel__header"><h2>가맹 조건을 문서 기준으로 확인합니다</h2><p>브랜드 존재 여부와 출점 가능 여부를 분리해서 보여줍니다.</p></header><article className="surface"><table className="data-table"><tbody><tr><th>브랜드 ID</th><td>{candidate.franchise.brand_id ?? '확인되지 않음'}</td></tr><tr><th>가맹 적격성</th><td><Badge tone={candidate.franchise.eligibility === 'VERIFIED' ? 'success' : 'warning'}>{candidate.franchise.eligibility}</Badge></td></tr><tr><th>출점 가능 여부</th><td><Badge tone={candidate.franchise.availability_status === 'AVAILABLE' ? 'success' : 'warning'}>{candidate.franchise.availability_status}</Badge></td></tr><tr><th>적격성 근거</th><td>{candidate.franchise.eligibility_evidence_refs.join(' · ') || '없음'}</td></tr><tr><th>정보공개서 근거</th><td>{candidate.franchise.disclosure_evidence_refs.join(' · ') || '없음'}</td></tr></tbody></table></article></>
}

function FundsPanel({ candidate }: { candidate: ResultCandidate }) {
  const finance = candidate.financial_summary
  return <><header className="panel__header"><h2>필요자금은 누락 비용까지 봅니다</h2><p>확인된 범위와 아직 계산할 수 없는 비용을 분리합니다.</p></header><div className="section-stack"><div className="fund-total"><span>현재 계산된 초기 필요자금</span><strong>{formatRange(finance.initial_cash)}</strong><small>근거 {finance.initial_cash.provenance_refs.length}건</small></div><article className="surface"><dl className="cost-list"><div className="cost-row"><dt>월 고정비</dt><dd>{formatRange(finance.monthly_fixed_cost)}</dd></div><div className="cost-row"><dt>월 손익분기 매출</dt><dd>{formatWon(finance.break_even_monthly_sales_krw)}</dd></div><div className="cost-row"><dt>일 필요 주문</dt><dd>{finance.required_daily_orders == null ? '확인되지 않음' : `${finance.required_daily_orders.toLocaleString('ko-KR')}건`}</dd></div></dl></article><article className="surface surface--flat"><div className="surface__head"><h3>아직 빠진 비용</h3></div>{finance.unknown_cost_fields.length ? <ul className="plain-list plain-list--neutral">{finance.unknown_cost_fields.map((field) => <li key={field}>{field}</li>)}</ul> : <p>현재 계산 계약에서 누락 비용이 발견되지 않았습니다.</p>}</article></div></>
}

function RisksPanel({ candidate }: { candidate: ResultCandidate }) {
  return <><header className="panel__header"><h2>판단을 뒤집을 조건부터 확인합니다</h2><p>위험, 반대 조건, 다음 행동을 실제 결과 그대로 표시합니다.</p></header><div className="section-stack"><div className="warning-box" role="note"><span aria-hidden="true">!</span><p><strong>이 결과는 최종 창업 결정을 대신하지 않습니다.</strong> 계약, 송금, 대출과 최종 창업 여부는 사용자가 결정합니다.</p></div><article className="surface"><div className="surface__head"><h3>주요 위험</h3></div>{candidate.risks.length ? <ul className="plain-list">{candidate.risks.map((risk) => <li key={risk.risk_id}><div><strong>{risk.severity} · {risk.risk_id}</strong><p>{risk.summary}</p></div></li>)}</ul> : <p>등록된 위험이 없습니다.</p>}</article><article className="surface"><div className="surface__head"><h3>판단 전환 조건</h3></div>{candidate.counterfactuals.length ? <ul className="plain-list plain-list--neutral">{candidate.counterfactuals.map((item) => <li key={`${item.variable}-${item.condition}`}><div><strong>{item.variable}: {item.condition}</strong><p>{item.decision_impact}</p></div></li>)}</ul> : <p>등록된 판단 전환 조건이 없습니다.</p>}</article><article className="surface"><div className="surface__head"><h3>다음 검증 행동</h3></div><ol className="condition-list">{candidate.next_actions.map((action) => <li key={action}><div><strong>{action}</strong></div></li>)}</ol></article></div></>
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
      setStatus(next.status === 'CLARIFICATION_REQUIRED' ? '추가 설명이 필요합니다.' : next.status === 'REVIEW_REQUIRED' ? '적용 전 변경안을 확인해 주세요.' : `변경안 상태: ${next.status}`)
    } catch (error) { setStatus(error instanceof Error ? error.message : '변경안을 만들지 못했습니다.') } finally { setBusy(false) }
  }

  const cancel = async () => {
    if (!preview) return
    setBusy(true)
    try { await client.cancelFeedback(projectId, preview.preview_id); setPreview(null); setStatus('변경안을 취소했습니다. 현재 결과는 바뀌지 않았습니다.') } catch (error) { setStatus(error instanceof Error ? error.message : '변경안을 취소하지 못했습니다.') } finally { setBusy(false) }
  }

  const confirm = async () => {
    if (!preview?.proposal_digest) return
    setBusy(true)
    try {
      const resolution = await client.confirmFeedback(projectId, preview)
      if (resolution.workflow) {
        const progress = await waitForWorkflow(client, projectId, resolution.workflow, (next) => setStatus(`결과 재계산 ${next.completed_stage_count}/${next.total_stage_count}`))
        if (!['SUCCEEDED', 'PARTIAL'].includes(progress.status)) throw new Error(`재계산이 완료되지 않았습니다: ${progress.status}`)
        onResult(await client.getResult(projectId))
      }
      setDraft(''); setPreview(null); setStatus('확인한 변경안을 반영하고 결과를 갱신했습니다.')
    } catch (error) { setStatus(error instanceof Error ? error.message : '변경안을 반영하지 못했습니다.') } finally { setBusy(false) }
  }

  const changedFields = preview ? Object.keys(preview.after_founder ?? {}).filter((key) => JSON.stringify(preview.before_founder[key]) !== JSON.stringify(preview.after_founder?.[key])) : []
  return <section className="feedback-panel" id="resultFeedback" aria-labelledby="feedbackTitle"><div className="feedback-panel__head"><Badge tone="accent">결과 생성 후 사용</Badge><h2 id="feedbackTitle">결과 피드백</h2><p>확인 전에는 권위 상태와 결과가 바뀌지 않습니다.</p></div><form className="feedback-form" onSubmit={submit}><div className="field"><label htmlFor="feedbackInput">자연어 피드백</label><div className="feedback-compose"><textarea id="feedbackInput" ref={inputRef} value={draft} disabled={busy || preview !== null} onChange={(event) => setDraft(event.target.value)} placeholder="예: 저가 브랜드는 빼고 10평 이하로 보고 싶어" /><button className="btn btn--primary" disabled={busy || preview !== null} type="submit">{busy ? '처리 중' : '제안 만들기'}</button></div></div></form>{preview && <section className="proposal" aria-labelledby="proposalTitle"><div className="proposal__head"><h3 id="proposalTitle">적용 전 변경 확인</h3><p>상태: {preview.status} · 아직 결과에는 반영되지 않았습니다.</p></div>{changedFields.map((field) => <div className="diff-row" key={field}><span className="diff-label">{field}</span><div className="diff-values"><span className="diff-old">{String(preview.before_founder[field] ?? '없음')}</span><span>→</span><span className="diff-new">{String(preview.after_founder?.[field] ?? '없음')}</span></div></div>)}{preview.clarifying_questions.map((question) => <p key={question}>{question}</p>)}{preview.risk_flags.length > 0 && <p>주의: {preview.risk_flags.join(' · ')}</p>}<div className="feedback-actions"><button className="btn" disabled={busy} type="button" onClick={cancel}>제안 취소</button><button className="btn btn--primary" disabled={busy || preview.status !== 'REVIEW_REQUIRED' || !preview.proposal_digest} type="button" onClick={confirm}>{busy ? '변경 적용 중' : '변경 적용'}</button></div></section>}<p className="feedback-status" aria-live="polite">{status}</p><p className="feedback-note">계약, 결제, 본사 연락은 자동으로 실행하지 않습니다.</p></section>
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
    try { const next = await client.selectCandidate(project.project_id, result, activeCandidate.candidate_id); setSelection(next); setActionStatus(`${activeCandidate.display_name}을 다음 준비 대상으로 선택했습니다.`) } catch (error) { setActionStatus(error instanceof Error ? error.message : '후보를 선택하지 못했습니다.') } finally { setSelectionBusy(false) }
  }
  const updateResult = (next: ResultView) => { setResult(next); setActiveCandidateIndex(0); setActivePanel('overview'); setSelection(null) }
  if (!activeCandidate) return <main className="analysis-stage"><h1>표시할 후보가 없습니다</h1><p>결과 계약을 다시 확인해 주세요.</p></main>

  return <div className="shell min-h-dvh"><header className="topbar"><a className="wordmark" href="#top">CaffeMate</a><div className="topbar__meta"><Badge tone={result.freshness === 'CURRENT' ? 'success' : 'warning'}>{result.freshness}</Badge><span className="version">결과 생성 {createdAt}</span></div></header><main className="page" id="top">{candidates.length > 1 && <section className="candidate-picker" aria-labelledby="candidatePickerTitle"><div className="candidate-picker__head"><div><p className="candidate-picker__count">검토 후보 {candidates.length}개</p><h2 id="candidatePickerTitle">추천안부터 살펴보세요</h2></div><p>모든 후보는 같은 결과 계약으로 비교합니다.</p></div><div className="candidate-tabs" role="tablist" aria-label="창업안 후보">{candidates.map((candidate, index) => <button id={`candidate-tab-${index}`} className="candidate-tab" type="button" role="tab" aria-selected={activeCandidateIndex === index} aria-controls="candidate-report" tabIndex={activeCandidateIndex === index ? 0 : -1} data-recommended={candidate.is_primary_next_review || undefined} key={candidate.candidate_id} onClick={() => { setActiveCandidateIndex(index); setActivePanel('overview') }}><span className="candidate-tab__number">{candidate.rank ? `${candidate.rank}순위` : '순위 없음'}</span><strong>{candidate.display_name}</strong><small>{statusLabel(candidate.review_status)} · {formatRange(candidate.financial_summary.initial_cash)}</small></button>)}</div></section>}<section className="intro" aria-labelledby="pageTitle"><div className="intro__copy"><div className="context-line"><Badge>{activeCandidate.case_type === 'FRANCHISE' ? '프랜차이즈' : '개인카페'}</Badge><Badge tone={statusTone(activeCandidate.review_status)}>{statusLabel(activeCandidate.review_status)}</Badge><Badge tone={result.audit_status === 'PASSED' ? 'success' : 'warning'}>감사 {result.audit_status}</Badge></div><h1 id="pageTitle">{activeCandidate.display_name}</h1><p className="intro__lede">{activeCandidate.summary}</p></div>{result.freshness === 'STALE' && <div className="demo-notice" role="note"><span className="demo-notice__mark">!</span><p>현재 결과는 이전 상태를 기준으로 생성되었습니다. 변경된 항목: {result.stale_head_dimensions.join(' · ')}</p></div>}</section><div className="mobile-switcher"><label htmlFor="sectionSelect">결과 항목</label><select className="section-select" id="sectionSelect" value={activePanel} onChange={(event) => setActivePanel(event.target.value as PanelName)}>{panels.map((panel) => <option value={panel.id} key={panel.id}>{panel.label}</option>)}</select></div><div className="workbench"><aside className="rail"><div className="rail__inner"><FeedbackPanel client={client} projectId={project.project_id} onResult={updateResult} /><ResultNav activePanel={activePanel} onChange={setActivePanel} /></div></aside><div className="panels" id="candidate-report"><ActivePanel panel={activePanel} project={project} candidate={activeCandidate} key={`${activeCandidate.candidate_id}-${activePanel}`} /><aside className="action-dock"><p className="action-dock__status" aria-live="polite">{actionStatus}</p><div className="action-group"><button className="btn btn--primary" disabled={selectionBusy || selection?.candidate_id === activeCandidate.candidate_id} onClick={select}>{selectionBusy ? '선택 반영 중' : selection?.candidate_id === activeCandidate.candidate_id ? '선택 완료' : '다음 준비 대상으로 선택'}</button><button className="btn btn--accent" onClick={() => document.getElementById('feedbackInput')?.focus()}>피드백으로 이동</button></div>{selection && <p className="table-note">필요 자료 {selection.required_evidence.length}건 · 점포 입력 {selection.property_intake_enabled ? '가능' : '불가'} · 문서 입력 {selection.document_intake_enabled ? '가능' : '불가'}</p>}</aside></div></div></main><footer className="footer"><strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span><span>상태 버전 {result.current_head.state_version}</span></footer></div>
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
    } catch (error) { setLoginError(error instanceof Error ? error.message : 'Google 로그인에 실패했습니다.') } finally { setLoginBusy(false) }
  }

  const completeOnboarding = async (values: OnboardingValues) => {
    if (!client || !project) throw new Error('프로젝트 연결이 준비되지 않았습니다.')
    const confirmedProject = await client.confirmOnboarding(project.project_id, values)
    setProject(confirmedProject)
    const workflow = await client.startFirstProposal(project.project_id)
    const terminal = await waitForWorkflow(client, project.project_id, workflow, setProgress)
    if (!['SUCCEEDED', 'PARTIAL'].includes(terminal.status)) {
      const reasons = terminal.terminal_reason_codes.join(' · ')
      throw new Error(`첫 분석이 완료되지 않았습니다: ${terminal.status}${reasons ? ` (${reasons})` : ''}`)
    }
    const nextResult = await client.getResult(project.project_id)
    setResult(nextResult); setScreen('result'); window.scrollTo({ top: 0 })
  }

  if (screen === 'welcome') return <Welcome onStart={start} busy={loginBusy} error={loginError} />
  if (screen === 'onboarding') return <><Onboarding onComplete={completeOnboarding} />{progress && <p className="workflow-progress" aria-live="polite">분석 진행 {progress.completed_stage_count}/{progress.total_stage_count} · {progress.current_stage_codes.join(' · ') || progress.status}</p>}</>
  if (client && project && result) return <ResultScreen client={client} project={project} initialResult={result} />
  return <main className="analysis-stage"><h1>결과를 불러오지 못했습니다</h1><p>프로젝트 상태를 다시 확인해 주세요.</p></main>
}
