import type { ResultCandidate } from '../apiClient'

const evidenceLabels: Record<string, string> = {
  SITE_PLAN_AND_AUTHORITY_CONFIRMATION: '점포 도면과 관할기관 확인',
  CURRENT_FACILITY_PHOTOS: '현재 시설 사진·현황',
  FIRE_SAFETY_CONFIRMATION: '소방 적용사항 확인 결과',
  CURRENT_FIRE_EQUIPMENT_STATUS: '현재 소방시설 현황',
  LANDLORD_WRITTEN_CONSENT: '임대인 또는 관리주체 서면 동의',
  LEASE_SPECIAL_TERMS: '임대차계약 특약·관리규정',
  SIGNAGE_PLAN: '간판 크기·위치·조명 계획',
  SIGNAGE_AUTHORITY_CONFIRMATION: '옥외광고물 담당부서 확인',
  CURRENT_CONTRACTED_POWER: '현재 계약전력·전기설비 현황',
  ELECTRICAL_UPGRADE_CONFIRMATION: '전력 증설 가능 여부 확인',
  BUILDING_REGISTER: '건축물대장',
  AUTHORITY_USE_CONFIRMATION: '관할기관 업종 가능 여부 확인',
  DATED_HQ_WRITTEN_CONFIRMATION: '날짜가 있는 본사 서면 확인',
  HQ_TERRITORY_CONFIRMATION: '본사 영업지역 보호범위 확인',
  LATEST_FRANCHISE_AGREEMENT_DRAFT: '최신 가맹계약서 교부본',
  HQ_SITE_APPROVAL: '본사 점포 승인',
  HQ_DESIGN_APPROVAL: '본사 설계·인테리어 승인',
}

const resolverLabels: Record<string, string> = {
  LOCAL_AUTHORITY: '관할 행정기관',
  FIRE_AUTHORITY: '관할 소방기관',
  LANDLORD_OR_BUILDING_MANAGER: '임대인·건물 관리주체',
  UTILITY_OR_BUILDING_MANAGER: '공급기관·건물 관리주체',
  FRANCHISE_HQ: '프랜차이즈 본사',
}

export function ExternalChecks({ candidate }: { candidate: ResultCandidate }) {
  const requirements = candidate.verification_requirements ?? []
  if (!requirements.length) return null
  return (
    <section id="result-external" className="result-section external-checks" role="region" aria-labelledby="externalChecksTitle">
      <header className="result-section__head">
        <p className="result-kicker">제품 밖에서 확정</p>
        <h2 id="externalChecksTitle">CaffeMate 밖에서 확인해야 해요</h2>
        <p>현재 계산과 별개로, 이 점포에서 실제 진행하려면 외부 주체가 최종 확인해야 하는 항목입니다.</p>
      </header>
      <p className="external-checks__summary">총 {requirements.length}개 · 아래 확인은 CaffeMate가 대신 승인·신고·연락하지 않습니다.</p>
      <ul className="action-list">
        {requirements.map((requirement) => (
          <li key={requirement.requirement_code}>
            <div>
              <strong>{requirement.label}</strong>
              <p>{requirement.reason}</p>
              <small>
                {requirement.authority
                  ? `최종 확인: ${requirement.authority}`
                  : `확인 주체: ${resolverLabels[requirement.resolver] ?? requirement.resolver}`}
              </small>
              {requirement.required_evidence.length > 0 && (
                <div className="external-checks__evidence">
                  <b>확인에 필요한 것</b>
                  <div className="external-checks__evidence-items">
                    {requirement.required_evidence.map((evidence) => (
                      <span key={evidence}>{evidenceLabels[evidence] ?? evidence}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <span>외부 확인</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
