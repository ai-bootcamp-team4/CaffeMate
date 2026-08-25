import type { ResultCandidate } from '../apiClient'
import { decisionInputLabel, provenanceLabel, refinableInputs } from './resultPresentation'

export function RefinableUnknowns({ candidate }: { candidate: ResultCandidate }) {
  const inputs = refinableInputs(candidate)
  return (
    <section className="result-section" aria-labelledby="refinableTitle">
      <header className="result-section__head">
        <p className="result-kicker">CaffeMate에서 더 정밀하게</p>
        <h2 id="refinableTitle">자료를 넣으면 다시 판단할 수 있어요</h2>
        <p>이 값들은 실제 점포·견적·계약 자료가 들어오면 계산에 다시 반영됩니다.</p>
      </header>
      {inputs.length ? (
        <ul className="action-list">
          {inputs.map((input) => (
            <li key={input.field}>
              <div><strong>{decisionInputLabel(input)}</strong><p>{provenanceLabel(input)} · {input.applied_to.length ? `${input.applied_to.length}개 계산 항목에 반영` : '재계산 입력'}</p></div>
              <span>{input.resolution_action?.type === 'PROPERTY_TERMS' ? '실제 점포 입력' : input.resolution_action?.type === 'DOCUMENT_INTAKE' ? '문서로 교체' : '조건 입력'}</span>
            </li>
          ))}
        </ul>
      ) : <p>현재 추가 자료로 다시 계산할 항목이 없습니다.</p>}
    </section>
  )
}
