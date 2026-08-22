import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { McpConnector, McpConnectorRegistry, McpScopeContext } from './router'
import { createSourceHealthConnector, type McpConfiguredSource } from './source-health'

const JUSO_SOURCE_ID = 'mois-juso-address-search'
const JUSO_SOURCE_REF = 'https://business.juso.go.kr/addrlink/addrLinkApi.do'
const JUSO_GUIDE_REF = 'https://business.juso.go.kr/jst/jstRoadNmAddrApiSearch'
const LEGAL_DONG_SOURCE_ID = 'mois-legal-dong-directory'
const LEGAL_DONG_SOURCE_REF = 'https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000052&nttId=124059'
const LEGAL_DONG_DATA_DATE = '2026-03-01'
const LEGAL_DONG_BOUNDARY_VERSION = 'MOIS_LEGAL_DONG_20260301'
const JUSO_REQUEST_TIMEOUT_MS = 8_000
const JUSO_REQUEST_ATTEMPTS = 2

interface ConnectorOptions {
  jusoApiKey?: string
  fetch?: typeof globalThis.fetch
  now?: () => Date
  sourceHealthSources?: readonly McpConfiguredSource[]
  useLegalDongDirectory?: boolean
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

interface LegalDongEntry {
  administrativeCode: string
  components: string[]
  displayName: string
  normalizedComponents: string[]
  normalizedDisplayName: string
}

const LEGAL_DONG_DIRECTORY_TEXT = readFileSync(
  resolve(process.cwd(), 'mcp/data/legal-dongs-20260301.tsv'),
  'utf8',
)
const LEGAL_DONG_DIRECTORY_DIGEST = digest(LEGAL_DONG_DIRECTORY_TEXT)
const LEGAL_DONG_DIRECTORY: readonly LegalDongEntry[] = Object.freeze(
  LEGAL_DONG_DIRECTORY_TEXT.trim().split('\n').map((line) => {
    const [administrativeCode, sido, sigungu, eupmyeondong, dongri] = line.split('\t')
    if (!administrativeCode || !/^\d{10}$/.test(administrativeCode) || !sido) {
      throw new Error('LEGAL_DONG_DIRECTORY_INVALID')
    }
    const components = [sido, sigungu, eupmyeondong, dongri].filter(Boolean) as string[]
    const displayName = components.join(' ')
    return {
      administrativeCode,
      components,
      displayName,
      normalizedComponents: components.map(normalizeSearchText),
      normalizedDisplayName: normalizeSearchText(displayName),
    }
  }),
)

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

function directoryMatchScore(entry: LegalDongEntry, tokens: string[]): number | null {
  if (!tokens.length) return null
  let score = 0
  for (const token of tokens) {
    const tokenScore = Math.max(...entry.normalizedComponents.map((component, index) => {
      const levelWeight = [0, 20, 40, 10][index] ?? 0
      const suffixWeight = component.startsWith(token)
        ? component.includes(`${token}동`) ? 30
          : component.includes(`${token}읍`) ? 10
            : component.includes(`${token}면`) ? 5
              : 0
        : 0
      if (component === token) return 500 + levelWeight
      if (component.startsWith(token)) return 400 + levelWeight + suffixWeight
      if (component.includes(token)) return 250 + levelWeight
      return 0
    }))
    if (tokenScore === 0) return null
    score += tokenScore
  }
  return score
}

function searchLegalDongDirectory(query: string, limit: number): RankedAreaCandidate[] {
  const tokens = queryTokens(query)
  const normalizedQuery = tokens.join('')
  return LEGAL_DONG_DIRECTORY
    .map((entry) => ({ entry, score: directoryMatchScore(entry, tokens) }))
    .filter((item): item is { entry: LegalDongEntry; score: number } => item.score !== null)
    .sort((left, right) => right.score - left.score
      || left.entry.displayName.localeCompare(right.entry.displayName, 'ko-KR'))
    .slice(0, limit)
    .map(({ entry }) => {
      const locality = entry.normalizedComponents.at(-1) ?? ''
      return {
        administrative_code: entry.administrativeCode,
        display_name: entry.displayName,
        boundary_version: LEGAL_DONG_BOUNDARY_VERSION,
        match_kind: entry.normalizedDisplayName === normalizedQuery || locality === normalizedQuery
          ? 'EXACT'
          : locality.startsWith(normalizedQuery)
            ? 'ALIAS'
            : 'CONTAINS',
        score: directoryMatchScore(entry, tokens) ?? 0,
      }
    })
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
  const components = [
    { value: row.siNm, levelWeight: 0 },
    { value: row.sggNm, levelWeight: 20 },
    { value: row.emdNm, levelWeight: 40 },
    { value: row.liNm, levelWeight: 10 },
    { value: row.hemdNm, levelWeight: 30 },
  ]
    .filter((component): component is { value: string; levelWeight: number } => Boolean(component.value))
    .map((component) => ({ ...component, value: normalizeSearchText(component.value) }))
  if (!components.length || !tokens.length) return null

  let score = 0
  for (const token of tokens) {
    const tokenScore = Math.max(...components.map(({ value, levelWeight }) => {
      const suffixWeight = value.startsWith(token)
        ? value.includes(`${token}동`) ? 30
          : value.includes(`${token}읍`) ? 10
            : value.includes(`${token}면`) ? 5
              : 0
        : 0
      if (value === token) return 400 + levelWeight
      if (value.startsWith(token)) return 300 + levelWeight + suffixWeight
      if (value.includes(token)) return 200 + levelWeight
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
  const displayName = [
    row.siNm,
    row.sggNm,
    row.emdNm,
    row.liNm && row.liNm !== row.emdNm ? row.liNm : undefined,
  ].filter(Boolean).join(' ')
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

function legalDongSourceTrace(now: Date) {
  return [{
    source_id: LEGAL_DONG_SOURCE_ID,
    source_ref: LEGAL_DONG_SOURCE_REF,
    data_date: LEGAL_DONG_DATA_DATE,
    retrieved_at: now.toISOString(),
    content_digest: LEGAL_DONG_DIRECTORY_DIGEST,
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
    const input = rawInput as { query: string; limit: number }
    const directoryData = options.useLegalDongDirectory === false
      ? []
      : searchLegalDongDirectory(input.query, input.limit)
    if (directoryData.length) {
      return {
        ...base(scope, 'resolve_area', now),
        status: 'OK',
        data: directoryData.map((candidate) => ({
          administrative_code: candidate.administrative_code,
          display_name: candidate.display_name,
          boundary_version: candidate.boundary_version,
          match_kind: candidate.match_kind,
        })),
        missing_fields: [],
        source_trace: legalDongSourceTrace(now),
      }
    }

    const jusoApiKey = options.jusoApiKey
    if (!jusoApiKey) return unavailable(scope, 'resolve_area', now, 'SOURCE_CREDENTIAL_MISSING')
    const fetchJusoOnce = async (query: string, count: number) => {
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
    const fetchJuso = async (query: string, count: number) => {
      let lastError: unknown
      for (let attempt = 0; attempt < JUSO_REQUEST_ATTEMPTS; attempt += 1) {
        try {
          return await fetchJusoOnce(query, count)
        } catch (error) {
          lastError = error
        }
      }
      throw lastError
    }

    let originalBody: string
    try {
      originalBody = await fetchJuso(input.query, input.limit)
    } catch {
      return { ...base(scope, 'resolve_area', now), status: 'PARTIAL', data: [], missing_fields: ['administrative_area'], source_trace: [], error_codes: ['SOURCE_UNAVAILABLE'] }
    }
    let originalParsed: JusoResult
    try {
      originalParsed = JSON.parse(originalBody) as JusoResult
    } catch {
      return { ...base(scope, 'resolve_area', now), status: 'PARTIAL', data: [], missing_fields: ['administrative_area'], source_trace: sourceTrace(now, originalBody), error_codes: ['SOURCE_RESPONSE_INVALID'] }
    }
    if (originalParsed.results?.common?.errorCode !== '0') {
      return { ...base(scope, 'resolve_area', now), status: 'PARTIAL', data: [], missing_fields: ['administrative_area'], source_trace: sourceTrace(now, originalBody), error_codes: ['SOURCE_RESPONSE_REJECTED'] }
    }

    const tokens = queryTokens(input.query)
    const originalRows = (originalParsed.results?.juso ?? []).map((row) => ({ row, expanded: false }))
    let rows = originalRows
    let data = rankAndDeduplicate(rows, tokens, input.limit, false)
    const bodies = [originalBody]

    if (!data.length) {
      const expansions = localitySearchVariants(input.query)
      const expandedResponses = await Promise.allSettled(
        expansions.map((query) => fetchJuso(query, input.limit)),
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

export { JUSO_SOURCE_ID, LEGAL_DONG_SOURCE_ID }
