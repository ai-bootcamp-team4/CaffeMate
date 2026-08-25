import type { ResultCandidate, ResultDecisionDelta } from '../apiClient'
import { formatRange } from '../presentation'
import { publicStatus, resolutionStatusLabel } from '../result/resultPresentation'

function changeForCandidate(delta: ResultDecisionDelta | null | undefined, candidate: ResultCandidate) {
  if (!delta) return null
  return delta.candidate_changes.find((change) => change.display_name === candidate.display_name)
    ?? delta.candidate_changes[0]
    ?? null
}

type CandidateChange = NonNullable<ResultDecisionDelta['candidate_changes']>[number]

function capitalGateChange(change: CandidateChange | null) {
  return change?.gate_changes?.find((gate) => gate.gate_type === 'CAPITAL') ?? null
}

function recalculatedStatusLabel(
  status: ResultCandidate['review_status'] | null,
  phase: 'previous' | 'current',
  change: CandidateChange | null,
) {
  const capital = capitalGateChange(change)
  const gateStatus = phase === 'previous' ? capital?.previous_status : capital?.current_status
  if (status === 'EXCLUDED' && gateStatus === 'FAIL') return '자기자금만으로 부족'
  if (status === 'REVIEW_RECOMMENDED' && gateStatus === 'PASS') return '자기자금으로 충당 가능'
  if (status === 'CONDITIONAL_REVIEW' && gateStatus === 'CONDITIONAL') {
    return capital?.previous_status === 'FAIL' && phase === 'current'
      ? '대출 고려 시 검토 가능'
      : '실제 비용 범위 확인 필요'
  }
  if (status === 'CONDITIONAL_REVIEW') return '추가 확인 후 판단'
  return status ? publicStatus(status) : phase === 'previous' ? '이전 판정' : '현재 판정'
}

function gateStatusLabel(
  gate: NonNullable<CandidateChange['gate_changes']>[number],
  phase: 'previous' | 'current',
) {
  const status = phase === 'previous' ? gate.previous_status : gate.current_status
  if (gate.gate_type !== 'CAPITAL') {
    return status === 'PASS' ? '통과' : status === 'FAIL' ? '막힘' : status === 'CONDITIONAL' ? '추가 확인 필요' : phase === 'previous' ? '이전' : '현재'
  }
  if (status === 'FAIL') return '자기자금만으로 부족'
  if (status === 'PASS') return '자기자금으로 충당 가능'
  if (status === 'CONDITIONAL') {
    return gate.previous_status === 'FAIL' && phase === 'current'
      ? '대출 고려 시 검토 가능'
      : '실제 비용 범위 확인 필요'
  }
  return phase === 'previous' ? '이전' : '현재'
}

export function DecisionDelta({ delta, candidate, previousFinancialSummary }: {
  delta: ResultDecisionDelta | null | undefined
  candidate: ResultCandidate
  previousFinancialSummary: ResultCandidate['financial_summary']
}) {
  const change = changeForCandidate(delta, candidate)
  const statusChanged = change?.previous_review_status !== change?.current_review_status
  const maintainedStatus = recalculatedStatusLabel(candidate.review_status, 'current', change)
  return (
    <section className="refinement-section decision-delta" aria-labelledby="decisionDeltaTitle">
      <header className="refinement-section__head">
        <p className="result-kicker">다시 계산한 결과</p>
        <h2 id="decisionDeltaTitle">{statusChanged ? '무엇이 바뀌어서 판단이 달라졌나요?' : '입력값을 바꾼 뒤 무엇이 달라졌나요?'}</h2>
        <p>총액 차이만이 아니라 교체된 입력과 Gate 변화를 함께 확인합니다.</p>
      </header>
      <p className="decision-delta__status-label">후보 검토 상태</p>
      {statusChanged ? (
        <div className="decision-delta__status">
          <span>{recalculatedStatusLabel(change?.previous_review_status ?? null, 'previous', change)}</span>
          <strong>→</strong>
          <span>{maintainedStatus}</span>
        </div>
      ) : (
        <div className="decision-delta__status">
          <strong>검토 상태 유지</strong>
          <span>{maintainedStatus}</span>
        </div>
      )}
      <div className="decision-delta__money">
        <div><span>초기 필요자금</span><strong>{formatRange(previousFinancialSummary.initial_cash)} → {formatRange(candidate.financial_summary.initial_cash)}</strong></div>
        <div><span>월 고정비</span><strong>{formatRange(previousFinancialSummary.monthly_fixed_cost)} → {formatRange(candidate.financial_summary.monthly_fixed_cost)}</strong></div>
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
          {change.gate_changes.map((gate) => (
            <li key={gate.gate_type}>
              {gate.gate_type === 'CAPITAL' ? '자금 조건' : '판정 조건'}: {gate.previous_status === gate.current_status
                ? `유지 · ${gateStatusLabel(gate, 'current')}`
                : `${gateStatusLabel(gate, 'previous')} → ${gateStatusLabel(gate, 'current')}`}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
