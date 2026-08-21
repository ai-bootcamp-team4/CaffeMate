import { type FormEvent, type KeyboardEvent, useReducer, useRef } from 'react'
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

function Badge({ children, tone = '' }: { children: React.ReactNode; tone?: string }) {
  return <span className={`badge ${tone ? `badge--${tone}` : ''}`}>{children}</span>
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

function OverviewPanel() {
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
            <p>초기 자금 범위 안에 들어올 가능성은 있지만, 실제 임대조건과 본사 필수 구매비용을 합치면 한도를 넘을 수 있습니다.</p>
          </div>
          <div className="judgement__aside">
            <strong>지금 결정하지 않는 이유</strong>
            <span>정보공개서 최신본 미확인</span>
            <span>점포·견적 미연결</span>
          </div>
        </div>
        <article className="surface" aria-labelledby="summaryTitle">
          <div className="surface__head">
            <h3 id="summaryTitle">창업안 요약</h3>
            <p>브랜드와 운영 형태를 한 개의 비교 가능한 창업안으로 묶었습니다.</p>
          </div>
          <dl className="summary-grid">
            <div className="summary-item"><dt>브랜드</dt><dd>브랜드 A</dd><small>실제 브랜드 아님</small></div>
            <div className="summary-item"><dt>점포 규모</dt><dd>약 10–14평</dd><small>가상 목업값</small></div>
            <div className="summary-item"><dt>운영 방식</dt><dd>창업자 직접 운영 + 피크타임 보조</dd><small>가정값</small></div>
            <div className="summary-item"><dt>예상 총 필요자금</dt><dd>8,900만–1억 2,800만 원</dd><small>가상 목업값 · 누락 비용 있음</small></div>
          </dl>
        </article>
        <div className="split">
          <article className="surface surface--flat" aria-labelledby="whyTitle">
            <div className="surface__head"><h3 id="whyTitle">이 후보가 나온 이유</h3></div>
            <ul className="plain-list">
              <li>개인카페보다 메뉴와 운영 절차를 빠르게 갖출 수 있는 방향을 원했습니다.</li>
              <li>10–14평 소형점 가정이 현재 직접 운영 계획과 비교적 잘 맞습니다.</li>
              <li>병원·대학·주거 수요가 섞인 가상 상권에서 인지도가 초기 유입에 도움을 줄 가능성이 있습니다.</li>
            </ul>
          </article>
          <article className="surface" aria-labelledby="fitTitle">
            <div className="surface__head"><h3 id="fitTitle">창업자 적합도</h3><p>점수가 아니라 조건별 상태입니다.</p></div>
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
      <header className="panel__header"><h2>상권 신호와 근거 상태</h2><p>수치 자체보다 출처, 자료 시점, 추정 여부를 분리해서 읽습니다.</p></header>
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
      <header className="panel__header"><h2>가맹 조건을 문서 기준으로 확인합니다</h2><p>프랜차이즈 후보는 상권 적합성만이 아니라 본사 비용과 계약 통제를 함께 봐야 합니다.</p></header>
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

function FundsPanel() {
  const costs = [['가맹비·교육비', '1,430만 원'], ['인테리어', '2,500만–4,480만 원'], ['장비·집기', '3,000만–4,200만 원'], ['초도물품·개점 지원', '700만–1,100만 원'], ['초기 운영자금', '1,270만–1,590만 원']]
  return (
    <>
      <header className="panel__header"><h2>필요자금은 누락 비용까지 봅니다</h2><p>본사 안내 금액만 더하지 않고 임차비, 공사 변동, 초기 운영자금을 분리합니다.</p></header>
      <div className="section-stack">
        <div className="fund-total"><span>현재 계산된 총 필요자금</span><strong>8,900만–1억 2,800만 원</strong><small>가상 목업값 · 보증금, 권리금, 철거·증설비 일부 미포함</small></div>
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
      <header className="panel__header"><h2>판단을 뒤집을 조건부터 확인합니다</h2><p>좋은 이유만 나열하지 않고 후보를 제외할 수 있는 반증 조건을 먼저 둡니다.</p></header>
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

function ActivePanel({ panel }: { panel: PanelName }) {
  const content = { overview: <OverviewPanel />, market: <MarketPanel />, franchise: <FranchisePanel />, funds: <FundsPanel />, risks: <RisksPanel /> }[panel]
  return <section className="panel" id={`panel-${panel}`} role="tabpanel" aria-labelledby={`tab-${panel}`} tabIndex={0}>{content}</section>
}

export default function App() {
  const [state, dispatch] = useReducer(resultReducer, initialResultState)
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

  return (
    <div className="shell min-h-dvh">
      <header className="topbar">
        <a className="wordmark" href="#top" aria-label="CaffeMate 결과 상단으로">CaffeMate</a>
        <div className="topbar__meta"><Badge tone="accent">데모</Badge><span className="version">결과 버전 1 · 2026.08.21</span></div>
      </header>
      <main className="page" id="top">
        <section className="intro" aria-labelledby="pageTitle">
          <div className="intro__copy"><div className="context-line"><Badge>프랜차이즈 창업안</Badge><Badge tone="warning">추가 정보 필요</Badge></div><h1 id="pageTitle">브랜드 A 소형점</h1><p className="intro__lede">브랜드 인지도와 소형 운영 조건은 검토할 가치가 있습니다. 다만 정보공개서, 필수품목, 실제 점포 비용이 확인되기 전에는 실행 여부를 결정할 수 없습니다.</p></div>
          <div className="demo-notice" role="note"><span className="demo-notice__mark" aria-hidden="true">!</span><p><strong>수원 원천동·우만동은 가상 데모 지역입니다.</strong> 이 화면의 금액, 점포 규모, 상권 수치와 브랜드 조건은 모두 제품 구조를 확인하기 위한 가상 목업값이며 실제 브랜드 추천이나 수익 예측이 아닙니다.</p></div>
        </section>
        <div className="mobile-switcher"><label htmlFor="sectionSelect">결과 항목</label><select className="section-select" id="sectionSelect" value={state.activePanel} onChange={(event) => dispatch({ type: 'panel.changed', panel: event.target.value as PanelName })}>{panels.map((panel) => <option value={panel.id} key={panel.id}>{panel.label}</option>)}</select></div>
        <div className="workbench">
          <aside className="rail" aria-label="결과 탐색과 피드백"><div className="rail__inner"><FeedbackPanel state={state} dispatch={dispatch} /><ResultNav state={state} dispatch={dispatch} /></div></aside>
          <div className="panels">
            <ActivePanel panel={state.activePanel} />
            <aside className="action-dock" aria-label="후보 처리">
              <p className="action-dock__status" data-tone={state.candidateTone || undefined} aria-live="polite">{state.actionStatus}</p>
              <div className="action-group">
                <button className="btn btn--primary" aria-busy={state.candidateBusy === 'primary'} disabled={state.candidateBusy !== null} onClick={() => settleCandidate('primary', '브랜드 A 소형점을 주력 후보로 선택했습니다.')}>{state.candidateBusy === 'primary' ? '선택 반영 중' : '주력 후보 선택'}</button>
                <button className="btn" aria-busy={state.candidateBusy === 'compare'} disabled={state.candidateBusy !== null} onClick={() => settleCandidate('compare', '브랜드 A 소형점을 비교 후보에 보관했습니다.')}>{state.candidateBusy === 'compare' ? '보관 중' : '비교 후보 보관'}</button>
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
