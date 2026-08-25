import type { ResultCandidate } from '../apiClient'
import { decisionHeading, gateCopy, gateMetricRows, gateTitle } from './resultPresentation'

export function DecisionReasons({ candidate }: { candidate: ResultCandidate }) {
  const title = decisionHeading(candidate.review_status)
  const gates = candidate.decision_trace?.gates ?? []
  return (
    <section className="result-section" role="region" aria-labelledby="decisionReasonsTitle">
      <header className="result-section__head">
        <p className="result-kicker">CaffeMate가 판정한 것</p>
        <h2 id="decisionReasonsTitle">{title}</h2>
        <p>확인된 입력과 계산으로 실제 판정에 영향을 준 조건만 보여줍니다.</p>
      </header>
      {gates.length ? (
        <div className="decision-grid">
          {gates.map((gate) => (
            <article className="decision-card" data-status={gate.status.toLowerCase()} key={`${gate.gate_type}-${gate.reason_code}`}>
              <div className="decision-card__head">
                <span>{gate.status === 'PASS' ? '통과' : gate.status === 'FAIL' ? '막는 조건' : '추가 입력 필요'}</span>
                <strong>{gateTitle(gate)}</strong>
              </div>
              <p>{gateCopy(gate)}</p>
              {gateMetricRows(gate).length > 0 && (
                <dl className="decision-metrics">
                  {gateMetricRows(gate).map((row) => (
                    <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>
                  ))}
                </dl>
              )}
            </article>
          ))}
        </div>
      ) : (
        <p className="contract-gap">판정 상태는 받았지만 세부 Gate 근거가 아직 공개 응답에 포함되지 않았습니다.</p>
      )}
    </section>
  )
}
