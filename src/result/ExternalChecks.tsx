import type { ResultCandidate } from '../apiClient'

export function ExternalChecks({ candidate }: { candidate: ResultCandidate }) {
  const requirements = candidate.verification_requirements ?? []
  return (
    <section id="result-external" className="result-section external-checks" role="region" aria-labelledby="externalChecksTitle">
      <header className="result-section__head">
        <p className="result-kicker">제품 밖에서 확정</p>
        <h2 id="externalChecksTitle">CaffeMate 밖에서 확인해야 해요</h2>
        <p>자료를 더 넣어도 CaffeMate가 최종 승인할 수 없는 항목입니다.</p>
      </header>
      {requirements.length ? (
        <ul className="action-list">
          {requirements.map((requirement) => (
            <li key={requirement.requirement_code}>
              <div>
                <strong>{requirement.label}</strong>
                <p>{requirement.reason}</p>
                <small>{requirement.authority ? `최종 확인: ${requirement.authority}` : `확인 주체: ${requirement.resolver}`}</small>
              </div>
              <span>외부 확인</span>
            </li>
          ))}
        </ul>
      ) : <p>현재 구조화된 외부 확인 요구사항이 없습니다.</p>}
    </section>
  )
}
