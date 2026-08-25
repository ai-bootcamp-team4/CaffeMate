import type { AreaSearchCandidate } from '../apiClient'

export type SupportedAnalysisKey = 'SEOUL_SEONGSU_1GA' | 'SEOUL_SEONGSU_2GA'

export interface SupportedAreaScenario {
  analysis_key: SupportedAnalysisKey
  area: AreaSearchCandidate
}

interface SearchEntry {
  candidate: AreaSearchCandidate
  aliases: string[]
  priority: number
  analysisKey?: SupportedAnalysisKey
}

const sourceRevision = 'MOIS_LEGAL_DONG_20260301'

function candidate(code: string, displayName: string): AreaSearchCandidate {
  return {
    area_id: `legal-dong:${code}`,
    scope_type: 'LEGAL_DONG',
    display_name: displayName,
    legal_dong_code: code,
    administrative_dong_codes: [],
    mapping_status: 'UNVERIFIED',
    source_revision: sourceRevision,
    boundary_version: null,
    selection_token: `area-selection:${code}:20260825`,
  }
}

const entries: SearchEntry[] = [
  {
    candidate: candidate('1120011400', '서울특별시 성동구 성수동1가'),
    aliases: ['성수', '성수동', '서울 성수', '성수1가', '성수동1가'],
    priority: 100,
    analysisKey: 'SEOUL_SEONGSU_1GA',
  },
  {
    candidate: candidate('1120011500', '서울특별시 성동구 성수동2가'),
    aliases: ['성수', '성수동', '서울 성수', '성수2가', '성수동2가'],
    priority: 99,
    analysisKey: 'SEOUL_SEONGSU_2GA',
  },
  {
    candidate: candidate('4575036000', '전북특별자치도 임실군 성수면'),
    aliases: ['성수', '성수면', '임실 성수'],
    priority: 70,
  },
  {
    candidate: candidate('4575036021', '전북특별자치도 임실군 성수면 도인리'),
    aliases: ['성수', '성수면', '도인리', '임실 성수 도인리'],
    priority: 60,
  },
  {
    candidate: candidate('4575036022', '전북특별자치도 임실군 성수면 봉강리'),
    aliases: ['성수', '성수면', '봉강리', '임실 성수 봉강리'],
    priority: 59,
  },
  {
    candidate: candidate('4575036023', '전북특별자치도 임실군 성수면 삼봉리'),
    aliases: ['성수', '성수면', '삼봉리', '임실 성수 삼봉리'],
    priority: 58,
  },
  {
    candidate: candidate('4575036024', '전북특별자치도 임실군 성수면 양지리'),
    aliases: ['성수', '성수면', '양지리', '임실 성수 양지리'],
    priority: 57,
  },
  {
    candidate: candidate('4575036025', '전북특별자치도 임실군 성수면 왕방리'),
    aliases: ['성수', '성수면', '왕방리', '임실 성수 왕방리'],
    priority: 56,
  },
]

function normalize(value: string) {
  return value.toLocaleLowerCase('ko-KR').replace(/\s+/g, '')
}

function matches(entry: SearchEntry, rawQuery: string) {
  const tokens = rawQuery.trim().split(/\s+/).map(normalize).filter(Boolean)
  if (!tokens.length) return false
  const haystack = [entry.candidate.display_name, ...entry.aliases].map(normalize).join('|')
  return tokens.every((token) => haystack.includes(token))
}

export function searchSimulationAreas(query: string): AreaSearchCandidate[] {
  return entries
    .filter((entry) => matches(entry, query))
    .sort((left, right) => right.priority - left.priority)
    .slice(0, 8)
    .map((entry) => ({ ...entry.candidate }))
}

export function simulationAreaByToken(selectionToken: string): SupportedAreaScenario | null {
  const entry = entries.find((value) => value.candidate.selection_token === selectionToken)
  if (!entry?.analysisKey) return null
  return { analysis_key: entry.analysisKey, area: { ...entry.candidate } }
}
