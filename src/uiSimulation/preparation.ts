import type { PreparationGuide, PreparationProcedure, ProcedureType, Project, ResultCandidate } from '../apiClient'

const asOf = '2026-08-25'
const retrievedAt = '2026-08-25T07:00:00Z'

const officialSources: Record<ProcedureType, { id: string; ref: string }> = {
  FACILITY_REQUIREMENTS: { id: 'source:food-facility', ref: 'https://www.foodsafetykorea.go.kr' },
  HYGIENE_EDUCATION: { id: 'source:food-hygiene', ref: 'https://www.foodsafetykorea.go.kr' },
  FOOD_SERVICE_REPORT: { id: 'source:gov-food-report', ref: 'https://www.gov.kr' },
  BUSINESS_REGISTRATION: { id: 'source:hometax-registration', ref: 'https://www.hometax.go.kr' },
  SIGNAGE: { id: 'source:seongdong-signage', ref: 'https://www.sd.go.kr' },
  FIRE_SAFETY: { id: 'source:nfa-fire', ref: 'https://www.nfa.go.kr' },
}

function procedure(
  type: ProcedureType,
  steps: Array<{ title: string; required: boolean; authority: string }>,
  options: { status?: PreparationProcedure['status']; missingFields?: string[] } = {},
): PreparationProcedure {
  const source = officialSources[type]
  return {
    procedure_type: type,
    status: options.status ?? 'OK',
    steps: steps.map((step, index) => ({
      procedure_type: type,
      step_order: index + 1,
      title: step.title,
      required: step.required,
      authority: step.authority,
      source_date: asOf,
      evidence_id: `evidence:procedure:${type.toLowerCase()}:${index + 1}`,
    })),
    missing_fields: options.missingFields ?? [],
    conflicts: [],
    error_codes: [],
    source_trace: [{
      source_id: source.id,
      source_ref: source.ref,
      data_date: asOf,
      retrieved_at: retrievedAt,
      content_digest: `sha256:${type.charCodeAt(0).toString(16).padStart(2, '0').repeat(32).slice(0, 64)}`,
    }],
    evidence_records: [],
  }
}

const procedures: PreparationProcedure[] = [
  procedure('FACILITY_REQUIREMENTS', [
    { title: '조리장·급수·환기·세척·화장실 시설기준을 점포 도면과 현장에서 대조', required: true, authority: '성동구청 위생 담당 부서' },
    { title: '배수·환기·냉장보관·손씻기 설비의 현재 상태와 공사 필요 범위를 확인', required: true, authority: '성동구청 위생 담당 부서' },
    { title: '인테리어 공사 전에 변경 계획을 들고 시설기준 적용사항을 사전 문의', required: false, authority: '성동구청 위생 담당 부서' },
  ]),
  procedure('HYGIENE_EDUCATION', [
    { title: '신규 영업자 위생교육 대상과 인정 교육기관을 확인', required: true, authority: '식품위생교육기관' },
    { title: '신규 영업자 위생교육 이수', required: true, authority: '식품위생교육기관' },
    { title: '영업신고에 사용할 교육 수료증을 준비', required: true, authority: '식품위생교육기관' },
  ]),
  procedure('FOOD_SERVICE_REPORT', [
    { title: '휴게음식점 영업신고 준비', required: true, authority: '성동구청 위생 담당 부서' },
    { title: '구비서류와 관할 접수처를 최종 확인', required: true, authority: '성동구청 위생 담당 부서' },
    { title: '시설기준 확인과 위생교육 이수 후 영업신고 서류를 제출', required: true, authority: '성동구청 위생 담당 부서' },
  ]),
  procedure('BUSINESS_REGISTRATION', [
    { title: '사업자등록에 필요한 임대차계약서·영업신고 관련 서류를 준비', required: true, authority: '관할 세무서 또는 국세청 홈택스' },
    { title: '사업자등록 신청 시 업태·종목과 사업장 주소를 확인해 제출', required: true, authority: '관할 세무서 또는 국세청 홈택스' },
    { title: '사업자등록증 발급 뒤 카드가맹·계좌·세금계산서 설정에 사용할 정보를 확인', required: false, authority: '국세청 홈택스' },
  ]),
  procedure('SIGNAGE', [
    { title: '설치할 간판의 종류·크기·조명·위치를 건물 외관 계획에 표시', required: true, authority: '성동구청 옥외광고물 담당 부서' },
    { title: '신고·허가 대상 여부와 수량 제한을 설치 전에 확인', required: true, authority: '성동구청 옥외광고물 담당 부서' },
    { title: '건물 관리규정과 임대인의 간판 설치 동의 범위를 함께 확인', required: false, authority: '건물 관리주체 및 임대인' },
  ]),
  procedure('FIRE_SAFETY', [
    { title: '점포 면적·층·수용인원·건물 용도에 따른 소방 적용사항을 확인', required: true, authority: '성동소방서' },
    { title: '소화기·유도등·비상구·감지기 등 현재 소방시설 상태를 현장에서 확인', required: true, authority: '성동소방서 또는 소방 점검 주체' },
    { title: '인테리어 공사로 소방시설 위치나 구획이 바뀌면 공사 전에 협의', required: true, authority: '성동소방서 또는 소방시설 관계자' },
  ]),
]

export function buildSeongsuPreparationGuide(
  project: Project,
  selectionId: string,
  candidate: ResultCandidate | undefined,
): PreparationGuide {
  return {
    project_id: project.project_id,
    selection_id: selectionId,
    candidate_id: candidate?.candidate_id ?? 'candidate:unselected',
    candidate_type: candidate?.case_type ?? 'INDEPENDENT',
    jurisdiction_code: project.state?.area.area_id ?? 'area:unresolved',
    jurisdiction_display_name: project.state?.area.display_name ?? '서울특별시 성동구 성수동',
    as_of: asOf,
    status: 'COMPLETE',
    procedures,
    source_trace: Object.values(officialSources).map((source, index) => ({
      source_id: source.id,
      source_ref: source.ref,
      data_date: asOf,
      retrieved_at: retrievedAt,
      content_digest: `sha256:${String(index + 1).repeat(64).slice(0, 64)}`,
    })),
    evidence_records: [],
    human_actions_only: true,
    external_submission_performed: false,
    generated_at: retrievedAt,
  }
}