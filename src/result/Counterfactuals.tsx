import type { ResultCandidate } from '../apiClient'
import { displayText, internalLabel } from '../presentation'

export function Counterfactuals({ candidate }: { candidate: ResultCandidate }) {
  if (!candidate.counterfactuals.length) return null
  return (
    <section className="result-section" aria-labelledby="counterfactualTitle">
      <header className="result-section__head">
        <p className="result-kicker">판단 반전 조건</p>
        <h2 id="counterfactualTitle">무엇이 바뀌면 판단도 바뀌나요?</h2>
      </header>
      <ul className="counterfactual-list">
        {candidate.counterfactuals.map((item) => (
          <li key={`${item.variable}-${item.condition}`}>
            <strong>{internalLabel(item.variable, '조건')} · {displayText(item.condition)}</strong>
            <p>{displayText(item.decision_impact)}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}
