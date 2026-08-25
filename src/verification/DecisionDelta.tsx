import type { ResultCandidate, ResultDecisionDelta } from '../apiClient'
import { formatWon } from '../presentation'
import { publicStatus, resolutionStatusLabel } from '../result/resultPresentation'

function changeForCandidate(delta: ResultDecisionDelta | null | undefined, candidate: ResultCandidate) {
  if (!delta) return null
  return delta.candidate_changes.find((change) => change.display_name === candidate.display_name)
    ?? delta.candidate_changes[0]
    ?? null
}

export function DecisionDelta({ delta, candidate, previousFinancialSummary }: {
  delta: ResultDecisionDelta | null | undefined
  candidate: ResultCandidate
  previousFinancialSummary: ResultCandidate['financial_summary']
}) {
  const change = changeForCandidate(delta, candidate)
  return (
    <section className="verification-section decision-delta" aria-labelledby="decisionDeltaTitle">
      <header className="verification-section__head">
        <p className="result-kicker">3 · 새 조건으로 다시 판단</p>
        <h2 id="decisionDeltaTitle">무엇이 바뀌어서 판단이 달라졌나요?</h2>
        <p>총액 차이만이 아니라 교체된 입력과 Gate 변화를 함께 확인합니다.</p>
      </header>
      <div className="decision-delta__status">
        <span>{change?.previous_review_status ? publicStatus(change.previous_review_status) : '이전 판정'}</span>
        <strong>→</strong>
        <span>{publicStatus(candidate.review_status)}</span>
      </div>
      <div className="decision-delta__money">
        <div><span>초기 필요자금 기준</span><strong>{formatWon(previousFinancialSummary.initial_cash.base)} → {formatWon(candidate.financial_summary.initial_cash.base)}</strong></div>
        <div><span>월 고정비 기준</span><strong>{formatWon(previousFinancialSummary.monthly_fixed_cost.base)} → {formatWon(candidate.financial_summary.monthly_fixed_cost.base)}</strong></div>
      </div>
      {change?.input_changes?.length ? (
        <div className="decision-delta__causes">
          {change.input_changes.map((inputChange) => (
            <article key={inputChange.field}>
              <strong>{inputChange.after?.label ?? inputChange.before?.label ?? '교체된 입력값'}</strong>
              <p>{inputChange.before ? resolutionStatusLabel(inputChange.before.resolution_status) : '이전 값 없음'} → {inputChange.after ? resolutionStatusLabel(inputChange.after.resolution_status) : '새 값 없음'}</p>
            </article>
          ))}
        </div>
      ) : (
        <p className="contract-gap">재계산 결과는 반영됐지만 입력 교체 원인 trace가 아직 공개 응답에 포함되지 않았습니다.</p>
      )}
      {change?.gate_changes?.length ? (
        <ul className="decision-delta__gates">
          {change.gate_changes.map((gate) => <li key={gate.gate_type}>{gate.gate_type === 'CAPITAL' ? '자금 조건' : '판정 조건'}: {gate.previous_status === 'PASS' ? '통과' : gate.previous_status === 'FAIL' ? '막힘' : '이전'} → {gate.current_status === 'PASS' ? '통과' : gate.current_status === 'FAIL' ? '막힘' : '현재'}</li>)}
        </ul>
      ) : null}
    </section>
  )
}
