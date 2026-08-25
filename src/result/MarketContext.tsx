import type { ResultCandidate } from '../apiClient'
import { formatDataDate, isHttpSource, marketSignalLabel, marketSignalValue } from '../presentation'

export function MarketContext({ candidate }: { candidate: ResultCandidate }) {
  const signals = (candidate.market_signals ?? []).filter((signal) => signal.decision_role === 'CONTEXT_ONLY')
  if (!signals.length) return null
  return (
    <section id="result-market" className="result-section result-section--muted" aria-labelledby="marketContextTitle">
      <header className="result-section__head">
        <p className="result-kicker">참고만 한 자료</p>
        <h2 id="marketContextTitle">같이 살펴본 상권 정보</h2>
        <p><strong>비용·예상매출·성공확률 계산에는 사용하지 않았어요.</strong> 지역 상황을 읽는 참고 정보입니다.</p>
      </header>
      <div className="market-context-grid">
        {signals.map((signal) => (
          <article key={signal.evidence_id}>
            <span>{marketSignalLabel(signal)}</span>
            <strong>{marketSignalValue(signal)}</strong>
            <small>{signal.source_title} · {formatDataDate(signal.data_date)}</small>
            <p>{signal.caveat}</p>
            {isHttpSource(signal.source_ref) && <a href={signal.source_ref} target="_blank" rel="noreferrer">공식 원문 보기</a>}
          </article>
        ))}
      </div>
    </section>
  )
}
