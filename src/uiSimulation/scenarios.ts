import type { AreaSearchCandidate } from '../apiClient'

export interface SimulationAreaScenario extends AreaSearchCandidate {
  profile_key: string
  aliases: string[]
  rent_scope: string
  cafe_count: number
  monthly_visits: number
  rent_multiplier: number
}

function scenario(
  slug: string,
  displayName: string,
  profileKey: string,
  aliases: string[],
  rentScope: string,
  cafeCount: number,
  monthlyVisits: number,
  rentMultiplier: number,
): SimulationAreaScenario {
  return {
    area_id: `ui-sim:${slug}`,
    scope_type: 'LEGAL_DONG',
    display_name: displayName,
    legal_dong_code: null,
    administrative_dong_codes: [],
    mapping_status: 'UNVERIFIED',
    source_revision: 'UI_SIMULATION_V1',
    boundary_version: null,
    selection_token: `ui-sim-area:${slug}`,
    profile_key: profileKey,
    aliases,
    rent_scope: rentScope,
    cafe_count: cafeCount,
    monthly_visits: monthlyVisits,
    rent_multiplier: rentMultiplier,
  }
}

export const simulationAreaCatalogue: SimulationAreaScenario[] = [
  scenario('seongsu-1', '서울특별시 성동구 성수동1가', 'seongsu', ['성수', '성수동', '서울 성수', '성수1가'], '서울 성동구 성수권', 86, 286_000, 1.42),
  scenario('seongsu-2', '서울특별시 성동구 성수동2가', 'seongsu', ['성수', '성수동', '서울 성수', '성수2가'], '서울 성동구 성수권', 94, 318_000, 1.48),
  scenario('seogyo', '서울특별시 마포구 서교동', 'hongdae', ['홍대', '홍대입구', '서교', '서울 홍대'], '서울 마포구 홍대권', 121, 342_000, 1.35),
  scenario('yeonnam', '서울특별시 마포구 연남동', 'hongdae', ['연남', '연트럴파크', '홍대 연남'], '서울 마포구 연남권', 73, 221_000, 1.24),
  scenario('gongdeok', '서울특별시 마포구 공덕동', 'office', ['공덕', '마포 공덕', '공덕역'], '서울 마포구 공덕권', 58, 204_000, 1.18),
  scenario('yeoksam', '서울특별시 강남구 역삼동', 'premium-office', ['강남', '역삼', '강남역', '서울 강남'], '서울 강남구 역삼권', 109, 401_000, 1.62),
  scenario('samseong', '서울특별시 강남구 삼성동', 'premium-office', ['강남', '삼성', '코엑스', '서울 강남'], '서울 강남구 삼성권', 82, 376_000, 1.58),
  scenario('jamsil', '서울특별시 송파구 잠실동', 'residential-commercial', ['잠실', '잠실새내', '송파 잠실'], '서울 송파구 잠실권', 96, 364_000, 1.39),
  scenario('pangyo', '경기도 성남시 분당구 판교동', 'newtown-office', ['판교', '분당 판교', '판교역'], '성남 분당구 판교권', 64, 248_000, 1.31),
  scenario('woncheon', '경기도 수원시 영통구 원천동', 'residential', ['원천', '원천동', '수원 원천', '영통 원천'], '수원 영통구 원천동', 42, 185_000, 1.0),
  scenario('ui-dong', '경기도 수원시 영통구 이의동', 'newtown-residential', ['광교', '광교신도시', '수원 광교', '이의동'], '수원 영통구 광교권', 55, 214_000, 1.15),
  scenario('jeonpo', '부산광역시 부산진구 전포동', 'regional-hotspot', ['전포', '전포카페거리', '부산 전포', '서면 전포'], '부산 부산진구 전포권', 88, 267_000, 1.12),
  scenario('u-dong-busan', '부산광역시 해운대구 우동', 'coastal-tourism', ['해운대', '부산 해운대', '우동', '센텀'], '부산 해운대구 우동권', 101, 391_000, 1.29),
  scenario('gung-dong', '대전광역시 유성구 궁동', 'university', ['궁동', '충남대', '대전 궁동', '유성 궁동'], '대전 유성구 궁동권', 47, 171_000, 0.88),
  scenario('yeon-dong-jeju', '제주특별자치도 제주시 연동', 'tourism-office', ['제주 연동', '제주시 연동', '신제주', '제주'], '제주시 연동권', 76, 302_000, 1.21),
  scenario('samdeok', '대구광역시 중구 삼덕동', 'regional-hotspot', ['삼덕', '대구 삼덕', '동성로 삼덕'], '대구 중구 삼덕권', 69, 226_000, 0.96),
  scenario('songdo', '인천광역시 연수구 송도동', 'newtown-office', ['송도', '인천 송도', '송도국제도시'], '인천 연수구 송도권', 71, 239_000, 1.16),
  scenario('dongmyeong', '광주광역시 동구 동명동', 'regional-hotspot', ['동명', '광주 동명', '동명동 카페거리'], '광주 동구 동명권', 61, 197_000, 0.91),
]

function normalize(value: string) {
  return value.toLocaleLowerCase('ko-KR').replace(/\s+/g, '')
}

function score(area: SimulationAreaScenario, rawQuery: string) {
  const query = normalize(rawQuery)
  if (!query) return 0
  const display = normalize(area.display_name)
  const aliases = area.aliases.map(normalize)

  if (display === query || aliases.includes(query)) return 100
  if (aliases.some((alias) => alias.startsWith(query))) return 80
  if (display.includes(query)) return 70
  if (aliases.some((alias) => alias.includes(query))) return 60

  const tokens = rawQuery.trim().split(/\s+/).map(normalize).filter(Boolean)
  const haystack = [display, ...aliases].join('|')
  if (tokens.length > 1 && tokens.every((token) => haystack.includes(token))) return 50
  return 0
}

export function searchSimulationAreas(query: string): SimulationAreaScenario[] {
  return simulationAreaCatalogue
    .map((area) => ({ area, score: score(area, query) }))
    .filter((entry) => entry.score > 0)
    .sort((left, right) => right.score - left.score || left.area.display_name.localeCompare(right.area.display_name, 'ko-KR'))
    .slice(0, 8)
    .map((entry) => entry.area)
}

export function simulationAreaByToken(selectionToken: string): SimulationAreaScenario | null {
  return simulationAreaCatalogue.find((area) => area.selection_token === selectionToken) ?? null
}