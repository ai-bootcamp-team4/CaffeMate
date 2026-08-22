import { createHash } from 'node:crypto'
import type { McpConnector, McpConnectorRegistry, McpScopeContext } from './router'
import { createSourceHealthConnector, type McpConfiguredSource } from './source-health'

const JUSO_SOURCE_ID = 'mois-juso-address-search'
const JUSO_SOURCE_REF = 'https://business.juso.go.kr/addrlink/addrLinkApi.do'
const JUSO_GUIDE_REF = 'https://business.juso.go.kr/jst/jstRoadNmAddrApiSearch'
const JUSO_REQUEST_TIMEOUT_MS = 12_000

interface ConnectorOptions {
  jusoApiKey?: string
  fetch?: typeof globalThis.fetch
  now?: () => Date
  sourceHealthSources?: readonly McpConfiguredSource[]
}

interface JusoResult {
  results?: {
    common?: { errorCode?: string; errorMessage?: string }
    juso?: JusoAddress[]
  }
}

interface JusoAddress {
  admCd?: string
  siNm?: string
  sggNm?: string
  emdNm?: string
  liNm?: string
  hemdNm?: string
  roadAddr?: string
  jibunAddr?: string
  bdNm?: string
}

interface RankedAreaCandidate {
  administrative_code: string
  display_name: string
  boundary_version: string
  match_kind: 'EXACT' | 'ALIAS' | 'CONTAINS' | 'AMBIGUOUS'
  score: number
}

const LOCALITY_SUFFIXES = ['동', '읍', '면', '리', '구', '시'] as const

function normalizeSearchText(value: string): string {
  return value.normalize('NFKC').toLocaleLowerCase('ko-KR').replaceAll(/\s+/g, '')
}

function queryTokens(query: string): string[] {
  return query
    .normalize('NFKC')
    .trim()
    .split(/\s+/)
    .map(normalizeSearchText)
    .filter(Boolean)
}

function localitySearchVariants(query: string): string[] {
  const parts = query.normalize('NFKC').trim().split(/\s+/).filter(Boolean)
  const last = parts.at(-1)
  if (!last || !/^[가-힣]{2,}$/.test(last) || LOCALITY_SUFFIXES.some((suffix) => last.endsWith(suffix))) {
    return []
  }
  const prefix = parts.slice(0, -1)
  return LOCALITY_SUFFIXES.map((suffix) => [...prefix, `${last}${suffix}`].join(' '))
}

function localityMatchScore(row: JusoAddress, tokens: string[]): number | null {
  const components = [row.siNm, row.sggNm, row.emdNm, row.liNm, row.hemdNm]
    .filter((value): value is string => Boolean(value))
    .map(normalizeSearchText)
  if (!components.length || !tokens.length) return null

  let score = 0
  for (const token of tokens) {
    const tokenScore = Math.max(...components.map((component) => {
      if (component === token) return 400
      if (component.startsWith(token)) return 300
      if (component.includes(token)) return 200
      return 0
    }))
    if (tokenScore === 0) return null
    score += tokenScore
  }
  return score
}

function addressFallbackScore(row: JusoAddress, tokens: string[]): number | null {
  const searchable = normalizeSearchText([row.roadAddr, row.jibunAddr, row.bdNm].filter(Boolean).join(' '))
  if (!searchable || !tokens.every((token) => searchable.includes(token))) return null
  return tokens.reduce((score, token) => score + (searchable.startsWith(token) ? 50 : 25), 0)
}

function toRankedCandidate(
  row: JusoAddress,
  tokens: string[],
  expanded: boolean,
  allowAddressFallback: boolean,
): RankedAreaCandidate | null {
  if (!row.admCd || !/^\d{10}$/.test(row.admCd)) return null
  const displayName = [row.siNm, row.sggNm, row.emdNm || row.liNm].filter(Boolean).join(' ')
  if (!displayName) return null
  const localityScore = localityMatchScore(row, tokens)
  const fallbackScore = allowAddressFallback ? addressFallbackScore(row, tokens) : null
  if (localityScore === null && fallbackScore === null) return null

  const normalizedDisplay = normalizeSearchText(displayName)
  const normalizedQuery = tokens.join('')
  return {
    administrative_code: row.admCd,
    display_name: displayName,
    boundary_version: 'JUSO_LIVE_UNVERSIONED',
    match_kind: normalizedDisplay === normalizedQuery
      ? 'EXACT'
      : localityScore !== null
        ? expanded ? 'ALIAS' : 'CONTAINS'
        : 'AMBIGUOUS',
    score: localityScore ?? fallbackScore ?? 0,
  }
}

function rankAndDeduplicate(
  rows: Array<{ row: JusoAddress; expanded: boolean }>,
  tokens: string[],
  limit: number,
  allowAddressFallback: boolean,
) {
  const unique = new Map<string, RankedAreaCandidate>()
  for (const { row, expanded } of rows) {
    const candidate = toRankedCandidate(row, tokens, expanded, allowAddressFallback)
    if (!candidate) continue
    const existing = unique.get(candidate.administrative_code)
    if (!existing || candidate.score > existing.score) unique.set(candidate.administrative_code, candidate)
  }
  return [...unique.values()]
    .sort((left, right) => right.score - left.score || left.display_name.localeCompare(right.display_name, 'ko-KR'))
    .slice(0, limit)
    .map((candidate) => ({
      administrative_code: candidate.administrative_code,
      display_name: candidate.display_name,
      boundary_version: candidate.boundary_version,
      match_kind: candidate.match_kind,
    }))
}

function digest(value: string): string {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function base(scope: McpScopeContext, toolName: string, now: Date) {
  return {
    schema_version: '1.0.0', request_id: scope.requestId, tool_name: toolName, tool_version: '1.0.0',
    project_id: scope.ventureProjectId, evidence_records: [], conflicts: [], error_codes: [],
    observed_at: now.toISOString(),
  }
}

function sourceTrace(now: Date, content: string) {
  return [{
    source_id: JUSO_SOURCE_ID, source_ref: JUSO_SOURCE_REF, data_date: null,
    retrieved_at: now.toISOString(), content_digest: digest(content),
  }]
}

function unavailable(scope: McpScopeContext, toolName: string, now: Date, code: string) {
  return {
    ...base(scope, toolName, now), status: 'PARTIAL', data: [],
    missing_fields: ['JUSO_API_KEY'], source_trace: [], error_codes: [code],
  }
}

export function createConnectorRegistry(options: ConnectorOptions = {}): McpConnectorRegistry {
  const fetcher = options.fetch ?? globalThis.fetch
  const clock = options.now ?? (() => new Date())

  const resolveArea: McpConnector = async (rawInput, scope) => {
    const now = clock()
    const jusoApiKey = options.jusoApiKey
    if (!jusoApiKey) return unavailable(scope, 'resolve_area', now, 'SOURCE_CREDENTIAL_MISSING')
    const input = rawInput as { query: string; limit: number }
    const fetchJuso = async (query: string, count: number) => {
      const url = new URL(JUSO_SOURCE_REF)
      url.search = new URLSearchParams({
        confmKey: jusoApiKey, currentPage: '1', countPerPage: String(count),
        keyword: query, resultType: 'json', hstryYn: 'N', firstSort: 'location', addInfoYn: 'Y',
      }).toString()
      const response = await fetcher(url, {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(JUSO_REQUEST_TIMEOUT_MS),
      })
      if (!response.ok) throw new Error(`HTTP_${response.status}`)
      return response.text()
    }

    let originalBody: string
    try {
      originalBody = await fetchJuso(input.query, Math.min(100, Math.max(20, input.limit * 5)))
    } catch {
      return { ...base(scope, 'resolve_area', now), status: 'ERROR', data: [], missing_fields: [], source_trace: [], error_codes: ['SOURCE_UNAVAILABLE'] }
    }
    let originalParsed: JusoResult
    try {
      originalParsed = JSON.parse(originalBody) as JusoResult
    } catch {
      return { ...base(scope, 'resolve_area', now), status: 'ERROR', data: [], missing_fields: [], source_trace: sourceTrace(now, originalBody), error_codes: ['SOURCE_RESPONSE_INVALID'] }
    }
    if (originalParsed.results?.common?.errorCode !== '0') {
      return { ...base(scope, 'resolve_area', now), status: 'ERROR', data: [], missing_fields: [], source_trace: sourceTrace(now, originalBody), error_codes: ['SOURCE_RESPONSE_REJECTED'] }
    }

    const tokens = queryTokens(input.query)
    const originalRows = (originalParsed.results?.juso ?? []).map((row) => ({ row, expanded: false }))
    let rows = originalRows
    let data = rankAndDeduplicate(rows, tokens, input.limit, false)
    const bodies = [originalBody]

    if (!data.length) {
      const expansions = localitySearchVariants(input.query)
      const expandedResponses = await Promise.allSettled(
        expansions.map((query) => fetchJuso(query, Math.min(100, Math.max(20, input.limit * 3)))),
      )
      for (const response of expandedResponses) {
        if (response.status !== 'fulfilled') continue
        let parsed: JusoResult
        try {
          parsed = JSON.parse(response.value) as JusoResult
        } catch {
          continue
        }
        if (parsed.results?.common?.errorCode !== '0') continue
        bodies.push(response.value)
        rows = rows.concat((parsed.results?.juso ?? []).map((row) => ({ row, expanded: true })))
      }
      data = rankAndDeduplicate(rows, tokens, input.limit, false)
    }

    if (!data.length) data = rankAndDeduplicate(originalRows, tokens, input.limit, true)
    return {
      ...base(scope, 'resolve_area', now), status: data.length ? 'OK' : 'NOT_FOUND', data,
      missing_fields: data.length ? [] : ['administrative_area'], source_trace: sourceTrace(now, bodies.join('\n')),
    }
  }

  const jusoHealthSource: McpConfiguredSource = {
    sourceId: JUSO_SOURCE_ID,
    dataDate: null,
    probeHealth: async ({ observedAt }) => {
      let probeBody: string
      let reachable: boolean
      let credentialValid = false
      try {
        const probeUrl = options.jusoApiKey
          ? new URL(JUSO_SOURCE_REF)
          : new URL(JUSO_GUIDE_REF)
        if (options.jusoApiKey) {
          probeUrl.search = new URLSearchParams({
            confmKey: options.jusoApiKey, currentPage: '1', countPerPage: '1',
            keyword: '세종대로 110', resultType: 'json',
          }).toString()
        }
        const response = await fetcher(probeUrl, {
          headers: { Accept: options.jusoApiKey ? 'application/json' : 'text/html' },
          signal: AbortSignal.timeout(5000),
        })
        probeBody = await response.text()
        reachable = response.ok
        if (reachable && options.jusoApiKey) {
          const parsed = JSON.parse(probeBody) as JusoResult
          credentialValid = parsed.results?.common?.errorCode === '0'
        }
      } catch {
        return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: null }
      }

      if (!reachable) return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: null }
      const sourceTrace = {
        sourceRef: options.jusoApiKey ? JUSO_SOURCE_REF : JUSO_GUIDE_REF,
        dataDate: null,
        contentDigest: digest(probeBody),
      }
      if (!options.jusoApiKey) {
        return { status: 'DEGRADED', lastSuccessAt: observedAt.toISOString(), dataDate: null, sourceTrace }
      }
      if (!credentialValid) return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: null, sourceTrace }
      return { status: 'HEALTHY', lastSuccessAt: observedAt.toISOString(), dataDate: null, sourceTrace }
    },
  }
  const getSourceHealth = createSourceHealthConnector(
    [jusoHealthSource, ...(options.sourceHealthSources ?? [])],
    clock,
  )

  return { resolve_area: resolveArea, get_source_health: getSourceHealth }
}

export { JUSO_SOURCE_ID }
