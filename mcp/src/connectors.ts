import { createHash } from 'node:crypto'
import type { McpConnector, McpConnectorRegistry, McpScopeContext } from './router'
import { createSourceHealthConnector, type McpConfiguredSource } from './source-health'

const JUSO_SOURCE_ID = 'mois-juso-address-search'
const JUSO_SOURCE_REF = 'https://business.juso.go.kr/addrlink/addrLinkApi.do'
const JUSO_GUIDE_REF = 'https://business.juso.go.kr/jst/jstRoadNmAddrApiSearch'

interface ConnectorOptions {
  jusoApiKey?: string
  fetch?: typeof globalThis.fetch
  now?: () => Date
  sourceHealthSources?: readonly McpConfiguredSource[]
}

interface JusoResult {
  results?: {
    common?: { errorCode?: string; errorMessage?: string }
    juso?: Array<{ admCd?: string; siNm?: string; sggNm?: string; emdNm?: string; liNm?: string; roadAddr?: string }>
  }
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
    if (!options.jusoApiKey) return unavailable(scope, 'resolve_area', now, 'SOURCE_CREDENTIAL_MISSING')
    const input = rawInput as { query: string; limit: number }
    const url = new URL(JUSO_SOURCE_REF)
    url.search = new URLSearchParams({
      confmKey: options.jusoApiKey, currentPage: '1', countPerPage: String(input.limit),
      keyword: input.query, resultType: 'json', hstryYn: 'N', firstSort: 'location', addInfoYn: 'Y',
    }).toString()
    let body: string
    try {
      const response = await fetcher(url, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(5000) })
      if (!response.ok) throw new Error(`HTTP_${response.status}`)
      body = await response.text()
    } catch {
      return { ...base(scope, 'resolve_area', now), status: 'ERROR', data: [], missing_fields: [], source_trace: [], error_codes: ['SOURCE_UNAVAILABLE'] }
    }
    let parsed: JusoResult
    try { parsed = JSON.parse(body) as JusoResult } catch {
      return { ...base(scope, 'resolve_area', now), status: 'ERROR', data: [], missing_fields: [], source_trace: sourceTrace(now, body), error_codes: ['SOURCE_RESPONSE_INVALID'] }
    }
    if (parsed.results?.common?.errorCode !== '0') {
      return { ...base(scope, 'resolve_area', now), status: 'ERROR', data: [], missing_fields: [], source_trace: sourceTrace(now, body), error_codes: ['SOURCE_RESPONSE_REJECTED'] }
    }
    const unique = new Map<string, { administrative_code: string; display_name: string; boundary_version: string; match_kind: string }>()
    for (const row of parsed.results.juso ?? []) {
      if (!row.admCd || !/^\d{10}$/.test(row.admCd)) continue
      const display = [row.siNm, row.sggNm, row.emdNm || row.liNm].filter(Boolean).join(' ')
      if (!display) continue
      unique.set(row.admCd, {
        administrative_code: row.admCd, display_name: display,
        boundary_version: 'JUSO_LIVE_UNVERSIONED',
        match_kind: display === input.query ? 'EXACT' : display.includes(input.query) ? 'CONTAINS' : 'AMBIGUOUS',
      })
    }
    const data = [...unique.values()].slice(0, input.limit)
    return {
      ...base(scope, 'resolve_area', now), status: data.length ? 'OK' : 'NOT_FOUND', data,
      missing_fields: data.length ? [] : ['administrative_area'], source_trace: sourceTrace(now, body),
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
