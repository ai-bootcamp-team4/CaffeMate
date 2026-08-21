export type LocationSuggestion = {
  district: string
  municipality: string
  value: string
  aliases: string[]
}

const locationSuggestions: LocationSuggestion[] = [
  { district: '성수동', municipality: '서울 성동구', value: '서울 성동구 성수동', aliases: ['성수', '성수동1가', '성수동2가'] },
  { district: '연남동', municipality: '서울 마포구', value: '서울 마포구 연남동', aliases: ['연남'] },
  { district: '망원동', municipality: '서울 마포구', value: '서울 마포구 망원동', aliases: ['망원'] },
  { district: '한남동', municipality: '서울 용산구', value: '서울 용산구 한남동', aliases: ['한남'] },
  { district: '을지로동', municipality: '서울 중구', value: '서울 중구 을지로동', aliases: ['을지로'] },
  { district: '원천동', municipality: '수원 영통구', value: '수원 영통구 원천동', aliases: ['원천', '아주대', '아주대학교'] },
  { district: '우만동', municipality: '수원 팔달구', value: '수원 팔달구 우만동', aliases: ['우만'] },
  { district: '행궁동', municipality: '수원 팔달구', value: '수원 팔달구 행궁동', aliases: ['행궁'] },
  { district: '전포동', municipality: '부산 부산진구', value: '부산 부산진구 전포동', aliases: ['전포', '전포카페거리'] },
  { district: '동명동', municipality: '광주 동구', value: '광주 동구 동명동', aliases: ['동명'] },
]

const normalize = (value: string) => value.toLocaleLowerCase('ko-KR').replace(/\s+/g, '')

export function findLocationSuggestions(query: string, limit = 5) {
  const normalizedQuery = normalize(query)
  if (!normalizedQuery) return []

  return locationSuggestions
    .map((suggestion) => {
      const candidates = [suggestion.district, suggestion.municipality, suggestion.value, ...suggestion.aliases].map(normalize)
      const exactPrefix = candidates.some((candidate) => candidate.startsWith(normalizedQuery))
      const partialMatch = candidates.some((candidate) => candidate.includes(normalizedQuery))
      return { suggestion, score: exactPrefix ? 0 : partialMatch ? 1 : 2 }
    })
    .filter(({ score }) => score < 2)
    .sort((a, b) => a.score - b.score || a.suggestion.district.localeCompare(b.suggestion.district, 'ko-KR'))
    .slice(0, limit)
    .map(({ suggestion }) => suggestion)
}
