import type { ResultCandidate } from '../apiClient'
import { formatRange } from '../presentation'
import { publicStatus, rankFactorLabel } from './resultPresentation'

export function CandidateComparison({ candidates, activeCandidateId, onSelect }: {
  candidates: ResultCandidate[]
  activeCandidateId: string
  onSelect: (candidateId: string) => void
}) {
  if (candidates.length < 2) return null
  return (
    <section className="result-section" aria-labelledby="candidateComparisonTitle">
      <header className="result-section__head">
        <p className="result-kicker">후보 비교</p>
        <h2 id="candidateComparisonTitle">왜 이 안을 먼저 보나요?</h2>
        <p>순위 자체보다 각 후보의 결정적 차이를 먼저 비교합니다.</p>
      </header>
      <div className="candidate-comparison">
        {candidates.map((candidate) => {
          const rankFactor = rankFactorLabel(candidate)
          return (
            <button
              key={candidate.candidate_id}
              className="candidate-comparison__item"
              data-active={candidate.candidate_id === activeCandidateId || undefined}
              aria-pressed={candidate.candidate_id === activeCandidateId}
              type="button"
              onClick={() => onSelect(candidate.candidate_id)}
            >
              <span>{candidate.rank ? `${candidate.rank}순위` : '순위 없음'} · {publicStatus(candidate.review_status)}</span>
              <strong>{candidate.display_name}</strong>
              <small>{formatRange(candidate.financial_summary.initial_cash)}</small>
              {rankFactor ? (
                <small>결정적 비교 기준: {rankFactor.label}{rankFactor.value ? ` · ${rankFactor.value}` : ''}</small>
              ) : (
                <small>세부 순위 근거가 제공되지 않았습니다.</small>
              )}
            </button>
          )
        })}
      </div>
    </section>
  )
}
