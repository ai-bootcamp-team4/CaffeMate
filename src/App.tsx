import { type FormEvent, type KeyboardEvent, useReducer, useRef, useState } from 'react'
import Onboarding from './Onboarding'
import Welcome from './Welcome'
import type { OnboardingValues } from './onboardingState'
import {
  buildFeedbackProposal,
  initialResultState,
  resultReducer,
  type PanelName,
  type ResultAction,
  type ResultState,
} from './resultState'

const panels: Array<{ id: PanelName; label: string }> = [
  { id: 'overview', label: '판단 요약' },
  { id: 'market', label: '상권 신호' },
  { id: 'franchise', label: '가맹 조건' },
  { id: 'funds', label: '필요자금' },
  { id: 'risks', label: '위험과 검증' },
]

type Candidate = {
  id: string
  navLabel: string
  name: string
  type: string
  status: string
  statusTone: string
  lede: string
  judgement: string
  holdReasons: string[]
  brand: string
  size: string
  operation: string
  funds: string
  reasons: string[]
}

const candidates: Candidate[] = [
  {
    id: 'candidate-a', navLabel: '추천 창업안', name: '브랜드 A 소형점', type: '프랜차이즈 창업안', status: '추가 정보 필요', statusTone: 'warning',
    lede: '브랜드 인지도와 소형 운영 조건은 검토할 가치가 있습니다. 다만 정보공개서, 필수품목, 실제 점포 비용이 확인되기 전에는 실행 여부를 결정할 수 없습니다.',
    judgement: '초기 자금 범위 안에 들어올 가능성은 있지만, 실제 임대조건과 본사 필수 구매비용을 합치면 한도를 넘을 수 있습니다.',
    holdReasons: ['정보공개서 최신본 미확인', '점포·견적 미연결'], brand: '브랜드 A', size: '약 10–14평', operation: '창업자 직접 운영 + 피크타임 보조', funds: '8,900만–1억 2,800만 원',
    reasons: ['메뉴와 운영 절차를 빠르게 갖출 수 있는 방향과 맞습니다.', '10–14평 소형점 가정이 현재 직접 운영 계획에 비교적 잘 맞습니다.', '인지도가 초기 유입에 도움을 줄 가능성이 있습니다.'],
  },
  {
    id: 'candidate-b', navLabel: '다른 후보 1', name: '브랜드 B 테이크아웃점', type: '프랜차이즈 창업안', status: '조건부 검토', statusTone: 'info',
    lede: '작은 면적과 간결한 메뉴 구성은 초기 비용 부담을 낮출 가능성이 있습니다. 배달·테이크아웃 수요와 최소 인력 운영이 실제 상권에서도 성립하는지 확인해야 합니다.',
    judgement: '점포 면적을 줄여 공사비를 낮출 여지가 있지만, 낮은 객단가를 보완할 주문량과 배달 수수료 조건이 아직 확인되지 않았습니다.',
    holdReasons: ['시간대별 주문량 미확인', '배달 수수료·임대료 미연결'], brand: '브랜드 B', size: '약 7–10평', operation: '창업자 직접 운영 + 최소 인력', funds: '7,600만–1억 900만 원',
    reasons: ['소형 점포를 우선 검토하려는 자금 조건과 맞습니다.', '간결한 메뉴 구성은 직접 운영 시 작업 복잡도를 낮출 수 있습니다.', '대학·주거 수요가 섞인 상권에서 테이크아웃 가설을 검증할 가치가 있습니다.'],
  },
  {
    id: 'candidate-c', navLabel: '다른 후보 2', name: '개인카페 컴팩트형', type: '개인카페 창업안', status: '추가 조사 필요', statusTone: 'warning',
    lede: '메뉴와 공간을 직접 설계할 수 있어 창업자의 취향을 가장 잘 반영할 수 있습니다. 브랜드 지원 없이 운영 체계와 초기 고객 유입을 직접 만들어야 하는 부담을 함께 검토해야 합니다.',
    judgement: '가맹비와 본사 지정 공사 부담은 줄일 수 있지만, 메뉴 개발·브랜딩·운영 매뉴얼 구축 비용과 시간이 아직 계산되지 않았습니다.',
    holdReasons: ['메뉴 원가·판매가 미확인', '초기 고객 유입 계획 필요'], brand: '개인카페 표준 모델', size: '약 9–12평', operation: '창업자 직접 운영', funds: '7,200만–1억 1,500만 원',
    reasons: ['직접 운영하며 매장 개성을 만들고 싶은 선호와 맞습니다.', '가맹비 없이 설비와 공간에 자금을 배분할 여지가 있습니다.', '메뉴와 운영 체계를 직접 검증할 준비가 되었는지 확인할 가치가 있습니다.'],
  },
]

function Badge({ children, tone = '' }: { children: React.ReactNode; tone?: string }) {
  return <span className={`badge ${tone ? `badge--${tone}` : ''}`}>{children}</span>
}

type ReportIllustrationName = 'recommendation' | 'market' | 'franchise' | 'funds' | 'risks'

function ReportIllustration({ name }: { name: ReportIllustrationName }) {
  const art = {
    recommendation: <><path d="M31 28h36v30a18 18 0 0 1-36 0V28Z" /><path d="M67 36h8a10 10 0 0 1 0 20h-8M26 72h50" /><path className="report-art__fill" d="M96 66c7-21 33-25 43-6 7 14-5 26-20 25-13-1-28-8-23-19Z" /><path d="M103 58c8 3 15 10 17 21m-5-25c7 5 12 12 13 23" /></>,
    market: <><path className="report-art__fill" d="M21 47h70v39H21z" /><path d="M15 47h82L87 28H25L15 47Zm17 39V60h18v26m12 0V60h18v26M15 86h82" /><path d="M118 35c-12 0-20 8-20 19 0 16 20 32 20 32s20-16 20-32c0-11-8-19-20-19Z" /><circle cx="118" cy="54" r="6" /></>,
    franchise: <><path className="report-art__fill" d="M31 18h66v73H31z" /><path d="M47 38h34M47 50h34M47 62h20m12 13 6 6 13-15" /><path d="M112 48h29v24a14.5 14.5 0 0 1-29 0V48Zm29 7h6a8 8 0 0 1 0 16h-6M108 90h40" /></>,
    funds: <><rect className="report-art__fill" x="19" y="22" width="62" height="68" rx="6" /><path d="M31 34h38v13H31zM32 60h8m10 0h8m10 0h2M32 72h8m10 0h8m10 0h2M32 84h8m10 0h8m10 0h2" /><path d="M100 20h40v68l-7-5-7 5-7-5-7 5-12-8V20Zm10 17h20m-20 13h20m-20 13h13" /></>,
    risks: <><path className="report-art__fill" d="M19 25h75v65H19z" /><path d="m32 44 6 6 11-14m-17 32 6 6 11-14m12-16h20M61 68h20" /><path d="M112 29h31l-4 53h-23l-4-53Zm3 12h25M109 82h37" /><path d="M120 22c0-5 4-8 8-8s8 3 8 8" /></>,
  }[name]

  return <svg className="report-art" viewBox="0 0 160 108" aria-hidden="true" focusable="false">{art}</svg>
}

function PanelHeader({ title, description, illustration }: { title: string; description: string; illustration: Exclude<ReportIllustrationName, 'recommendation'> }) {
  return <header className="panel__header panel__header--illustrated"><div><h2>{title}</h2><p>{description}</p></div><ReportIllustration name={illustration} /></header>
}

function ResultNav({ state, dispatch }: { state: ResultState; dispatch: React.Dispatch<ResultAction> }) {
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    let nextIndex = index
    if (event.key === 'ArrowDown') nextIndex = (index + 1) % panels.length
    if (event.key === 'ArrowUp') nextIndex = (index - 1 + panels.length) % panels.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = panels.length - 1
    const panel = panels[nextIndex]
    dispatch({ type: 'panel.changed', panel: panel.id })
    window.setTimeout(() => document.getElementById(`tab-${panel.id}`)?.focus({ preventScroll: true }), 0)
  }

  return (
    <nav className="result-nav" role="tablist" aria-orientation="vertical" aria-label="결과 상세 항목">
      <p className="rail__caption">결과 상세</p>
      {panels.map((panel, index) => (
        <button
          className="tab-button"
          role="tab"
          id={`tab-${panel.id}`}
          aria-controls={`panel-${panel.id}`}
          aria-selected={state.activePanel === panel.id}
          data-panel={panel.id}
          tabIndex={state.activePanel === panel.id ? 0 : -1}
          key={panel.id}
          onClick={() => dispatch({ type: 'panel.changed', panel: panel.id })}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {panel.label}
        </button>
      ))}
    </nav>
  )
}

function FeedbackPanel({ state, dispatch }: { state: ResultState; dispatch: React.Dispatch<ResultAction> }) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const proposalRef = useRef<HTMLElement>(null)
  const threadRef = useRef<HTMLDivElement>(null)
  const isReviewing = state.feedbackPhase !== 'editing'

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!state.feedbackDraft.trim()) {
      dispatch({ type: 'feedback.invalid' })
      inputRef.current?.focus()
      return
    }
    dispatch({ type: 'feedback.proposed', proposal: buildFeedbackProposal(state.feedbackDraft) })
    window.setTimeout(() => proposalRef.current?.focus({ preventScroll: true }), 0)
  }

  const apply = () => {
    dispatch({ type: 'feedback.apply.started' })
    window.setTimeout(() => {
      dispatch({ type: 'feedback.apply.completed' })
      window.setTimeout(() => {
        const thread = threadRef.current
        if (thread && typeof thread.scrollTo === 'function') {
          thread.scrollTo({ top: thread.scrollHeight })
        }
      }, 0)
    }, 650)
  }

  return (
    <section className="feedback-panel" id="resultFeedback" aria-labelledby="feedbackTitle">
      <div className="feedback-panel__head">
        <Badge tone="accent">결과 생성 후 사용</Badge>
        <h2 id="feedbackTitle">결과 피드백</h2>
        <p>완성된 결과만 조정하며 확인 전에는 반영하지 않습니다.</p>
      </div>

      <div className="feedback-thread" ref={threadRef} aria-label="피드백 대화 이력" aria-live="polite">
        {state.history.map((item) => (
          <div className={`chat-bubble chat-bubble--${item.role}`} key={item.id}>
            {item.text}
          </div>
        ))}
      </div>

      <div className="sample-row" aria-label="예시 피드백">
        {['저가 브랜드는 빼줘', '점포는 10평 이하로 보고 싶어'].map((sample, index) => (
          <button
            className="sample-chip"
            type="button"
            key={sample}
            disabled={isReviewing}
            onClick={() => {
              dispatch({ type: 'feedback.draft.changed', value: sample })
              inputRef.current?.focus()
            }}
          >
            {index === 0 ? sample : '10평 이하로'}
          </button>
        ))}
      </div>

      {!isReviewing && (
        <form className="feedback-form" onSubmit={submit} noValidate>
          <div className="field" data-state={state.feedbackTone}>
            <label htmlFor="feedbackInput">자연어 피드백</label>
            <div className="feedback-compose">
              <textarea
                id="feedbackInput"
                ref={inputRef}
                value={state.feedbackDraft}
                placeholder="예: 저가 브랜드는 빼줘"
                aria-describedby="feedbackMessage"
                aria-invalid={state.feedbackTone === 'error'}
                onChange={(event) => dispatch({ type: 'feedback.draft.changed', value: event.target.value })}
                required
              />
              <button className="btn btn--primary" type="submit" data-state={state.feedbackTone || undefined}>
                제안 만들기
              </button>
            </div>
            <p className="field__message" id="feedbackMessage" data-tone={state.feedbackTone || undefined}>
              {state.feedbackTone === 'error'
                ? '피드백이 비어 있습니다. 바꾸고 싶은 브랜드 방향이나 점포 규모를 적어 주세요.'
                : state.feedbackTone === 'success'
                  ? '적용된 조건을 바탕으로 다음 결과 버전을 만들 수 있습니다.'
                  : '전송하면 결과 변경안만 만듭니다.'}
            </p>
          </div>
        </form>
      )}

      {state.proposal && (
        <section
          className="proposal"
          id="feedbackProposal"
          aria-labelledby="proposalTitle"
          tabIndex={-1}
          ref={proposalRef}
        >
          <div className="proposal__head">
            <h3 id="proposalTitle">적용 전 변경 확인</h3>
            <p>아직 결과에는 반영되지 않았습니다.</p>
          </div>
          {state.proposal.brand && (
            <div className="diff-row">
              <span className="diff-label">브랜드 방향</span>
              <div className="diff-values">
                <span className="diff-old">{state.proposal.brand.before}</span>
                <span aria-hidden="true">→</span>
                <span className="diff-new">{state.proposal.brand.after}</span>
              </div>
            </div>
          )}
          {state.proposal.size && (
            <div className="diff-row">
              <span className="diff-label">점포 규모</span>
              <div className="diff-values">
                <span className="diff-old">{state.proposal.size.before}</span>
                <span aria-hidden="true">→</span>
                <span className="diff-new">{state.proposal.size.after}</span>
              </div>
            </div>
          )}
          <div className="diff-row">
            <span className="diff-label">적용 시 영향</span>
            <div>{state.proposal.impact}</div>
          </div>
          <div className="feedback-actions">
            <button
              className="btn"
              type="button"
              disabled={state.feedbackPhase === 'applying'}
              onClick={() => dispatch({ type: 'feedback.proposal.cancelled' })}
            >
              제안 취소
            </button>
            <button
              className="btn btn--primary"
              type="button"
              aria-busy={state.feedbackPhase === 'applying'}
              disabled={state.feedbackPhase === 'applying'}
              onClick={apply}
            >
              {state.feedbackPhase === 'applying' ? '변경 적용 중' : '변경 적용'}
            </button>
          </div>
        </section>
      )}

      {state.appliedConditions.length > 0 && (
        <p className="applied-conditions">적용 조건 · {state.appliedConditions.join(' · ')}</p>
      )}
      <p className="feedback-status" data-tone={state.feedbackTone || undefined} aria-live="polite">
        {state.feedbackStatus}
      </p>
      <p className="feedback-note">계약, 결제, 본사 연락은 자동으로 실행하지 않습니다.</p>
    </section>
  )
}

function OverviewPanel({ candidate }: { candidate: Candidate }) {
  const fitValues = candidate.id === 'candidate-a' ? [88, 62, 72, 84, 58] : candidate.id === 'candidate-b' ? [82, 78, 67, 76, 70] : [74, 68, 92, 61, 80]
  const fitLabels = ['운영', '자금', '취향', '상권', '확장']
  const radarPoint = (value: number, index: number) => {
    const angle = -Math.PI / 2 + index * (Math.PI * 2 / 5)
    const radius = 68 * value / 100
    return `${100 + Math.cos(angle) * radius},${100 + Math.sin(angle) * radius}`
  }
  const gridPoints = (ratio: number) => fitValues.map((_, index) => radarPoint(ratio * 100, index)).join(' ')
  return (
    <>
      <header className="panel__header">
        <h2>먼저 결론부터 검토합니다</h2>
        <p>추천 점수 대신 현재 판단, 맞는 이유, 막혀 있는 정보를 함께 보여줍니다.</p>
      </header>
      <div className="section-stack">
        <div className="judgement">
          <div className="judgement__status">
            <Badge tone="warning">현재 판단</Badge>
            <strong>계속 검토 · 추가 정보 필요</strong>
            <p>{candidate.judgement}</p>
          </div>
          <div className="judgement__aside">
            <strong>지금 결정하지 않는 이유</strong>
            {candidate.holdReasons.map((reason) => <span key={reason}>{reason}</span>)}
          </div>
        </div>
        <article className="surface" aria-labelledby="summaryTitle">
          <div className="surface__head">
            <h3 id="summaryTitle">창업안 요약</h3>
            <p>브랜드와 운영 형태를 한 개의 비교 가능한 창업안으로 묶었습니다.</p>
          </div>
          <dl className="summary-grid">
            <div className="summary-item"><dt>브랜드</dt><dd>{candidate.brand}</dd><small>실제 브랜드 아님</small></div>
            <div className="summary-item"><dt>점포 규모</dt><dd>{candidate.size}</dd><small>가상 목업값</small></div>
            <div className="summary-item"><dt>운영 방식</dt><dd>{candidate.operation}</dd><small>가정값</small></div>
            <div className="summary-item"><dt>예상 총 필요자금</dt><dd>{candidate.funds}</dd><small>가상 목업값 · 누락 비용 있음</small></div>
          </dl>
        </article>
        <div className="split">
          <article className="surface reason-panel" aria-labelledby="whyTitle">
            <div className="surface__head"><h3 id="whyTitle">이 후보가 나온 이유</h3></div>
            <ul className="plain-list">
              {candidate.reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </article>
          <article className="surface" aria-labelledby="fitTitle">
            <div className="surface__head"><h3 id="fitTitle">창업자 적합도</h3><p>입력 조건과 후보의 상대적인 맞음 정도입니다.</p></div>
            <figure className="fit-radar">
              <svg viewBox="0 0 200 200" role="img" aria-labelledby="fitChartTitle fitChartDesc">
                <title id="fitChartTitle">{candidate.name} 창업자 적합도</title>
                <desc id="fitChartDesc">운영, 자금, 취향, 상권, 확장성의 다섯 조건을 비교한 오각형 그래프</desc>
                {[1, .75, .5, .25].map((ratio) => <polygon className="fit-radar__grid" points={gridPoints(ratio)} key={ratio} />)}
                {fitValues.map((_, index) => <line className="fit-radar__axis" x1="100" y1="100" x2={radarPoint(100, index).split(',')[0]} y2={radarPoint(100, index).split(',')[1]} key={fitLabels[index]} />)}
                <polygon className="fit-radar__shape" points={fitValues.map(radarPoint).join(' ')} />
                {fitValues.map((value, index) => <circle className="fit-radar__dot" cx={radarPoint(value, index).split(',')[0]} cy={radarPoint(value, index).split(',')[1]} r="3" key={fitLabels[index]} />)}
              </svg>
              <figcaption>{fitLabels.map((label, index) => <span key={label}><b>{label}</b><small>{fitValues[index]}</small></span>)}</figcaption>
            </figure>
            <div className="fit-list">
              <div className="fit-row"><span>직접 운영 계획</span><Badge tone="success">잘 맞음</Badge></div>
              <div className="fit-row"><span>현재 자금 범위</span><Badge tone="warning">경계</Badge></div>
              <div className="fit-row"><span>브랜드 통제 수용</span><Badge tone="info">확인 필요</Badge></div>
              <div className="fit-row"><span>다점포 계획</span><Badge>해당 없음</Badge></div>
            </div>
          </article>
        </div>
      </div>
    </>
  )
}

const marketRows = [
  ['주요 연령대', '20–39세 비중 높음', '공공 인구자료 샘플', '확인 중', '관측'],
  ['시간대별 유동', '평일 점심·저녁 집중', '생활인구 샘플', '확인 중', '추정'],
  ['영업 중 카페', '반경 내 42곳', '인허가 자료 샘플', '기준일 미확인', '관측'],
  ['최근 개업·폐업', '개업 7 · 폐업 5', '인허가 이력 샘플', '확인 중', '계산'],
  ['카페 추정매출', '—', '확보되지 않음', '없음', '추정값만 허용'],
]

function MarketPanel() {
  return (
    <>
      <PanelHeader title="상권 신호와 근거 상태" description="수치 자체보다 출처, 자료 시점, 추정 여부를 분리해서 읽습니다." illustration="market" />
      <article className="surface">
        <div className="surface__head"><h3>가상 데모 상권 신호</h3><p>수원 원천동·우만동 가상 범위 · 모든 수치는 가상 목업값</p></div>
        <table className="data-table">
          <thead><tr><th>지표</th><th>현재 신호</th><th>자료 출처</th><th>신선도</th><th>성격</th></tr></thead>
          <tbody>{marketRows.map((row) => <tr key={row[0]}>{row.map((cell, index) => index === 0 ? <th scope="row" data-label="지표" key={cell}>{cell}</th> : <td data-label={['', '현재 신호', '자료 출처', '신선도', '성격'][index]} key={cell + index}>{index > 2 ? <Badge tone={cell.includes('확인') ? 'warning' : index === 4 ? 'info' : ''}>{cell}</Badge> : <div className="datum-main"><strong>{cell}</strong>{index === 1 && cell !== '—' && <small>가상 목업값</small>}</div>}</td>)}</tr>)}</tbody>
        </table>
        <p className="table-note">상권 단위 추정매출이 확보되더라도 개별 점포의 실제 매출 또는 예상매출로 바꾸어 표시하지 않습니다.</p>
      </article>
    </>
  )
}

const franchiseRows = [
  ['정보공개서 최신 여부', '—', '최신본 원문 필요', '미확인'],
  ['가맹비', '1,100만 원', '가상 목업값', '샘플'],
  ['교육비', '330만 원', '가상 목업값', '샘플'],
  ['계약 이행 보증금', '—', '본사 확인 필요', '미확인'],
  ['로열티', '월 매출의 3% 가정', '가상 목업값', '추정'],
  ['필수품목', '—', '품목·공급가 목록 필요', '핵심 미확인'],
  ['인테리어', '평당 250만–320만 원', '가상 목업값', '범위 추정'],
  ['계약기간', '—', '가맹계약서 필요', '미확인'],
  ['영업지역 보호', '—', '보호 범위 조항 필요', '미확인'],
]

function FranchisePanel() {
  return (
    <>
      <PanelHeader title="가맹 조건을 문서 기준으로 확인합니다" description="프랜차이즈 후보는 상권 적합성만이 아니라 본사 비용과 계약 통제를 함께 봐야 합니다." illustration="franchise" />
      <article className="surface">
        <div className="surface__head"><h3>정보공개서와 계약 핵심값</h3><p>확인되지 않은 값은 임의로 채우지 않고 —로 남깁니다.</p></div>
        <table className="data-table">
          <thead><tr><th>항목</th><th>현재 값</th><th>근거</th><th>상태</th></tr></thead>
          <tbody>{franchiseRows.map((row) => <tr key={row[0]}><th scope="row" data-label="항목">{row[0]}</th><td data-label="현재 값">{row[1]}</td><td data-label="근거">{row[2]}</td><td data-label="상태"><Badge tone={row[3].includes('미확인') ? 'warning' : 'info'}>{row[3]}</Badge></td></tr>)}</tbody>
        </table>
      </article>
    </>
  )
}

function FundsPanel({ candidate }: { candidate: Candidate }) {
  const costs = [['가맹비·교육비', '1,430만 원'], ['인테리어', '2,500만–4,480만 원'], ['장비·집기', '3,000만–4,200만 원'], ['초도물품·개점 지원', '700만–1,100만 원'], ['초기 운영자금', '1,270만–1,590만 원']]
  return (
    <>
      <PanelHeader title="필요자금은 누락 비용까지 봅니다" description="본사 안내 금액만 더하지 않고 임차비, 공사 변동, 초기 운영자금을 분리합니다." illustration="funds" />
      <div className="section-stack">
        <div className="fund-total"><span>현재 계산된 총 필요자금</span><strong>{candidate.funds}</strong><small>가상 목업값 · 보증금, 권리금, 철거·증설비 일부 미포함</small></div>
        <div className="split">
          <article className="surface"><div className="surface__head"><h3>현재 포함한 비용</h3></div><dl className="cost-list">{costs.map(([label, value]) => <div className="cost-row" key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="table-note">모든 금액은 가상 목업값입니다.</p></article>
          <article className="surface surface--flat"><div className="surface__head"><h3>아직 빠진 비용</h3><p>실제 점포와 계약서를 연결하면 총액이 커질 수 있습니다.</p></div><ul className="plain-list plain-list--neutral"><li>임대보증금·권리금·중개보수</li><li>전기 증설·급배수·환기 공사</li><li>폐기물 처리·철거·원상복구</li><li>필수품목 가격 차이와 물류비</li><li>부가세 포함 여부와 카드 수수료</li></ul></article>
        </div>
      </div>
    </>
  )
}

function RisksPanel() {
  return (
    <>
      <PanelHeader title="판단을 뒤집을 조건부터 확인합니다" description="좋은 이유만 나열하지 않고 후보를 제외할 수 있는 반증 조건을 먼저 둡니다." illustration="risks" />
      <div className="section-stack">
        <div className="warning-box" role="note"><span aria-hidden="true">!</span><p><strong>이 결과는 개별 점포의 실제 매출이 아닙니다.</strong> 상권 자료와 본사 자료는 검토 순서를 정하는 근거이며 성공을 보장하지 않습니다.</p></div>
        <div className="split">
          <article className="surface"><div className="surface__head"><h3>주요 위험</h3></div><ul className="plain-list"><li>본사 필수품목의 공급가와 가격 변경 조건이 수익성을 낮출 수 있습니다.</li><li>영업지역이 보호되지 않으면 인접 가맹점 출점 위험이 있습니다.</li><li>표준 공사와 필수 장비 때문에 소형점의 비용 절감 폭이 작을 수 있습니다.</li><li>계약 해지, 갱신, 양도 조건이 창업자의 선택을 제한할 수 있습니다.</li></ul></article>
          <article className="surface surface--flat"><div className="surface__head"><h3>판단 전환 조건</h3></div><ul className="plain-list plain-list--neutral"><li>실제 총 필요자금이 창업자 한도를 반복해서 초과하면 제외</li><li>필수품목 원가가 목표 원가율과 맞지 않으면 제외</li><li>영업지역 보호와 계약 해지 조건이 불리하면 보류</li><li>기존 시설 점포로 공사비를 낮추면 주력 후보로 상향 검토</li></ul></article>
        </div>
        <article className="surface"><div className="surface__head"><h3>다음 검증 행동</h3><p>판단을 크게 바꿀 가능성이 높은 순서입니다.</p></div><ol className="condition-list"><li><div><strong>정보공개서 최신본 확보</strong><p>가맹비, 로열티, 필수품목, 계약기간, 영업지역을 원문으로 확인합니다.</p></div></li><li><div><strong>가맹계약서 초안 비교</strong><p>해지·갱신·양도·위약금 조항을 정보공개서와 대조합니다.</p></div></li><li><div><strong>10–14평 점포 후보 3개 연결</strong><p>보증금, 월세, 관리비, 권리금, 시설 상태를 같은 형식으로 입력합니다.</p></div></li><li><div><strong>본사와 외부 견적 분리</strong><p>인테리어, 장비, 전기·급배수 공사의 포함 범위를 확인합니다.</p></div></li></ol></article>
        <article className="surface"><div className="surface__head"><h3>근거 목록</h3></div><table className="data-table"><thead><tr><th>근거</th><th>현재 상태</th><th>결과 활용</th></tr></thead><tbody>{[['사용자 온보딩', '확인됨', '자금·운영 선호 기준'], ['공공 상권 자료', '샘플', '수요·경쟁 가설'], ['정보공개서', '최신본 필요', '가맹 비용·계약 검증'], ['실제 점포 자료', '없음', '임차비·시설비 검증'], ['실제 견적', '없음', '총 필요자금 확정']].map((row) => <tr key={row[0]}><th data-label="근거">{row[0]}</th><td data-label="현재 상태"><Badge tone={row[1] === '확인됨' ? 'success' : row[1].includes('필요') || row[1] === '샘플' ? 'warning' : ''}>{row[1]}</Badge></td><td data-label="결과 활용">{row[2]}</td></tr>)}</tbody></table></article>
      </div>
    </>
  )
}

function ActivePanel({ panel, candidate }: { panel: PanelName; candidate: Candidate }) {
  const content = { overview: <OverviewPanel candidate={candidate} />, market: <MarketPanel />, franchise: <FranchisePanel />, funds: <FundsPanel candidate={candidate} />, risks: <RisksPanel /> }[panel]
  return <section className="panel" id={`panel-${panel}`} role="tabpanel" aria-labelledby={`tab-${panel}`} tabIndex={0}>{content}</section>
}

function ResultScreen() {
  const [state, dispatch] = useReducer(resultReducer, initialResultState)
  const [activeCandidateIndex, setActiveCandidateIndex] = useState(0)
  const activeCandidate = candidates[activeCandidateIndex]
  const feedbackInputId = 'feedbackInput'

  const settleCandidate = (action: 'primary' | 'compare', message: string) => {
    dispatch({ type: 'candidate.started', action })
    window.setTimeout(() => {
      dispatch({ type: 'candidate.completed', message })
      window.setTimeout(() => dispatch({ type: 'candidate.tone.cleared' }), 1600)
    }, 500)
  }

  const exclude = () => {
    dispatch({ type: 'candidate.excluded' })
    window.setTimeout(() => dispatch({ type: 'toast.hidden' }), 7000)
  }

  const moveToFeedback = () => {
    document.getElementById('resultFeedback')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    window.setTimeout(() => document.getElementById(feedbackInputId)?.focus({ preventScroll: true }), 240)
  }

  const switchCandidate = (index: number) => {
    setActiveCandidateIndex(index)
    dispatch({ type: 'panel.changed', panel: 'overview' })
  }

  const handleCandidateKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    let nextIndex = index
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % candidates.length
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + candidates.length) % candidates.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = candidates.length - 1
    switchCandidate(nextIndex)
    window.setTimeout(() => document.getElementById(`candidate-tab-${nextIndex}`)?.focus({ preventScroll: true }), 0)
  }

  return (
    <div className="shell min-h-dvh">
      <header className="topbar">
        <a className="wordmark" href="#top" aria-label="CaffeMate 결과 상단으로">CaffeMate</a>
        <div className="topbar__meta"><Badge tone="accent">데모</Badge><span className="version">결과 버전 1 · 2026.08.21</span></div>
      </header>
      <main className="page" id="top">
        {candidates.length > 1 && (
          <section className="candidate-picker" aria-labelledby="candidatePickerTitle">
            <div className="candidate-picker__head">
              <div><p className="candidate-picker__count">추천 1개 · 비교 후보 {candidates.length - 1}개</p><h2 id="candidatePickerTitle">추천안부터 살펴보세요</h2></div>
              <p>가장 먼저 볼 창업안을 추천안으로 두었습니다. 다른 후보도 눌러 같은 기준으로 비교할 수 있어요.</p>
              <ReportIllustration name="recommendation" />
            </div>
            <div className="candidate-tabs" role="tablist" aria-label="창업안 후보">
              {candidates.map((candidate, index) => (
                <button id={`candidate-tab-${index}`} className="candidate-tab" type="button" role="tab" aria-selected={activeCandidateIndex === index} aria-controls="candidate-report" aria-label={`${candidate.navLabel} ${candidate.name}`} tabIndex={activeCandidateIndex === index ? 0 : -1} data-recommended={index === 0 || undefined} key={candidate.id} onClick={() => switchCandidate(index)} onKeyDown={(event) => handleCandidateKeyDown(event, index)}>
                  <span className="candidate-tab__number">{candidate.navLabel}</span>
                  <strong>{candidate.name}</strong>
                  <small>{candidate.size} · {candidate.funds}</small>
                </button>
              ))}
            </div>
          </section>
        )}
        <section className="intro" aria-labelledby="pageTitle">
          <div className="intro__copy"><div className="context-line"><Badge>{activeCandidate.type}</Badge><Badge tone={activeCandidate.statusTone}>{activeCandidate.status}</Badge></div><h1 id="pageTitle">{activeCandidate.name}</h1><p className="intro__lede">{activeCandidate.lede}</p></div>
          <div className="demo-notice" role="note"><span className="demo-notice__mark" aria-hidden="true">!</span><p><strong>수원 원천동·우만동은 가상 데모 지역입니다.</strong> 이 화면의 금액, 점포 규모, 상권 수치와 브랜드 조건은 모두 제품 구조를 확인하기 위한 가상 목업값이며 실제 브랜드 추천이나 수익 예측이 아닙니다.</p></div>
        </section>
        <div className="mobile-switcher"><label htmlFor="sectionSelect">결과 항목</label><select className="section-select" id="sectionSelect" value={state.activePanel} onChange={(event) => dispatch({ type: 'panel.changed', panel: event.target.value as PanelName })}>{panels.map((panel) => <option value={panel.id} key={panel.id}>{panel.label}</option>)}</select></div>
        <div className="workbench">
          <aside className="rail" aria-label="결과 탐색과 피드백"><div className="rail__inner"><FeedbackPanel state={state} dispatch={dispatch} /><ResultNav state={state} dispatch={dispatch} /></div></aside>
          <div className="panels" id="candidate-report" role="tabpanel" aria-labelledby={`candidate-tab-${activeCandidateIndex}`}>
            <ActivePanel panel={state.activePanel} candidate={activeCandidate} key={`${activeCandidate.id}-${state.activePanel}`} />
            <aside className="action-dock" aria-label="후보 처리">
              <p className="action-dock__status" data-tone={state.candidateTone || undefined} aria-live="polite">{state.actionStatus}</p>
              <div className="action-group">
                <button className="btn btn--primary" aria-busy={state.candidateBusy === 'primary'} disabled={state.candidateBusy !== null} onClick={() => settleCandidate('primary', `${activeCandidate.name}을 주력 후보로 선택했습니다.`)}>{state.candidateBusy === 'primary' ? '선택 반영 중' : '주력 후보 선택'}</button>
                <button className="btn" aria-busy={state.candidateBusy === 'compare'} disabled={state.candidateBusy !== null} onClick={() => settleCandidate('compare', `${activeCandidate.name}을 비교 후보에 보관했습니다.`)}>{state.candidateBusy === 'compare' ? '보관 중' : '비교 후보 보관'}</button>
                <button className="btn" data-state={state.excluded ? 'error' : undefined} onClick={exclude}>제외</button>
                <button className="btn btn--accent" onClick={moveToFeedback}>피드백으로 이동</button>
              </div>
            </aside>
          </div>
        </div>
      </main>
      <footer className="footer"><strong>CaffeMate</strong><span>프랜차이즈 창업안 가상 목업 · 실제 투자 판단 자료 아님</span><span>결과 버전 1</span></footer>
      {state.toastVisible && <div className="toast" role="status" aria-live="polite"><span>후보를 제외했습니다.</span><button className="btn" type="button" onClick={() => dispatch({ type: 'candidate.exclude.undone' })}>되돌리기</button></div>}
    </div>
  )
}

export default function App() {
  const [screen, setScreen] = useState<'welcome' | 'onboarding' | 'result'>('welcome')
  const [, setFounderInput] = useState<OnboardingValues | null>(null)

  if (screen === 'welcome') {
    return <Welcome onStart={() => { setScreen('onboarding'); window.scrollTo({ top: 0 }) }} />
  }

  if (screen === 'onboarding') {
    return (
      <Onboarding
        onComplete={(values) => {
          setFounderInput(values)
          setScreen('result')
          window.scrollTo({ top: 0 })
        }}
      />
    )
  }

  return <ResultScreen />
}
