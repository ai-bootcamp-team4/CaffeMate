import { createHash } from 'node:crypto'
import type { McpConnector, McpScopeContext } from './router'

const BIGQUERY_API = 'https://bigquery.googleapis.com/bigquery/v2'
const QUERY_TIMEOUT_MS = 10_000
const REB_PAGE = 'https://www.reb.or.kr/r-one/portal/stat/easyStatPage'

interface PropertyReferenceOptions {
  projectId: string
  datasetId?: string
  location?: string
  accessToken: () => Promise<string>
  fetch?: typeof globalThis.fetch
  now?: () => Date
}

interface QueryField { v?: string | null }
interface QueryRow { f?: QueryField[] }
interface QueryResponse {
  jobComplete?: boolean
  rows?: QueryRow[]
  errors?: Array<{ reason?: string }>
}

interface PropertyReferencePayload {
  ingestion_id: string
  period_code: string
  region_code: string
  region_name: string
  property_class: 'SMALL_RETAIL' | 'MEDIUM_LARGE_RETAIL' | 'STRATA_RETAIL'
  effective_rent_krw_per_sqm_month: number
  conversion_rate_bps: number
  coverage_status: 'EXACT_MARKET' | 'PARENT_REGION'
  floor_basis: 'FIRST_FLOOR'
  rent_table_id: string
  conversion_table_id: string
  source_digests_json: string
}

const QUERY = `
WITH latest AS (
  SELECT ingestion_id, source_digests_json
  FROM \`__PROJECT__.__DATASET__.commercial_rent_manifest\`
  WHERE status = 'APPROVED'
    AND period_code <= FORMAT(
      '%04dQ%d',
      EXTRACT(YEAR FROM @as_of),
      DIV(EXTRACT(MONTH FROM @as_of) - 1, 3) + 1
    )
  ORDER BY loaded_at DESC
  LIMIT 1
)
SELECT TO_JSON_STRING(STRUCT(
  reference.ingestion_id,
  reference.period_code,
  reference.region_code,
  reference.region_name,
  reference.property_class,
  reference.effective_rent_krw_per_sqm_month,
  reference.conversion_rate_bps,
  reference.coverage_status,
  reference.floor_basis,
  reference.rent_table_id,
  reference.conversion_table_id,
  latest.source_digests_json
)) AS payload
FROM \`__PROJECT__.__DATASET__.commercial_rent_reference\` AS reference
JOIN latest USING (ingestion_id)
WHERE reference.region_code = SUBSTR(@administrative_code, 1, 2)
ORDER BY reference.property_class
`

function validateIdentifier(value: string, label: string): string {
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(value)) {
    throw new Error(`MCP_PROPERTY_REFERENCE_${label}_INVALID`)
  }
  return value
}

function periodEnd(period: string): string {
  const match = /^(\d{4})Q([1-4])$/.exec(period)
  if (!match) throw new Error('MCP_PROPERTY_REFERENCE_PERIOD_INVALID')
  const year = Number(match[1])
  const quarter = Number(match[2])
  return new Date(Date.UTC(year, quarter * 3, 0)).toISOString().slice(0, 10)
}

function page(tableId: string): string {
  if (!/^T[A-Z]?\d+$/.test(tableId)) throw new Error('MCP_PROPERTY_REFERENCE_TABLE_ID_INVALID')
  return `${REB_PAGE}/${tableId}.do`
}

function digestPair(payload: PropertyReferencePayload): string {
  const digests = JSON.parse(payload.source_digests_json) as Record<string, unknown>
  const rent = digests[payload.rent_table_id]
  const conversion = digests[payload.conversion_table_id]
  if (typeof rent !== 'string' || !/^[0-9a-f]{64}$/.test(rent)
    || typeof conversion !== 'string' || !/^[0-9a-f]{64}$/.test(conversion)) {
    throw new Error('MCP_PROPERTY_REFERENCE_DIGEST_MISSING')
  }
  return createHash('sha256').update(`${rent}:${conversion}`).digest('hex')
}

function evidenceId(payload: PropertyReferencePayload): string {
  const identity = [
    payload.ingestion_id,
    payload.period_code,
    payload.region_code,
    payload.property_class,
  ].join(':')
  return `reb-property:${createHash('sha256').update(identity).digest('hex')}`
}

function base(scope: McpScopeContext, now: Date) {
  return {
    schema_version: '1.0.0',
    request_id: scope.requestId,
    tool_name: 'get_property_reference',
    tool_version: '1.0.0',
    project_id: scope.ventureProjectId,
    evidence_records: [],
    conflicts: [],
    error_codes: [],
    observed_at: now.toISOString(),
  }
}

export function createPropertyReferenceConnector(options: PropertyReferenceOptions): McpConnector {
  const projectId = validateIdentifier(options.projectId, 'PROJECT')
  const datasetId = validateIdentifier(options.datasetId ?? 'caffemate_grounding', 'DATASET')
  const location = options.location ?? 'asia-northeast3'
  if (location !== 'asia-northeast3') throw new Error('MCP_PROPERTY_REFERENCE_LOCATION_INVALID')
  const fetcher = options.fetch ?? globalThis.fetch
  const clock = options.now ?? (() => new Date())

  return async (rawInput, scope, execution) => {
    const input = rawInput as {
      administrative_code: string
      boundary_version: string
      as_of: string
    }
    const now = clock()
    let response: Response
    try {
      const token = await options.accessToken()
      response = await fetcher(`${BIGQUERY_API}/projects/${projectId}/queries`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
          'X-Goog-User-Project': projectId,
        },
        body: JSON.stringify({
          query: QUERY.replaceAll('__PROJECT__', projectId).replaceAll('__DATASET__', datasetId),
          useLegacySql: false,
          location,
          timeoutMs: QUERY_TIMEOUT_MS,
          parameterMode: 'NAMED',
          queryParameters: [
            {
              name: 'administrative_code',
              parameterType: { type: 'STRING' },
              parameterValue: { value: input.administrative_code },
            },
            {
              name: 'as_of',
              parameterType: { type: 'DATE' },
              parameterValue: { value: input.as_of },
            },
          ],
        }),
        signal: execution?.signal,
      })
      if (!response.ok) throw new Error(`HTTP_${response.status}`)
    } catch {
      return {
        ...base(scope, now),
        status: 'ERROR',
        data: [],
        missing_fields: ['regional_property_reference'],
        source_trace: [],
        error_codes: ['PROPERTY_REFERENCE_QUERY_FAILED'],
      }
    }
    const queried = await response.json() as QueryResponse
    if (queried.errors?.length || queried.jobComplete === false) {
      return {
        ...base(scope, now),
        status: 'ERROR',
        data: [],
        missing_fields: ['regional_property_reference'],
        source_trace: [],
        error_codes: ['PROPERTY_REFERENCE_QUERY_FAILED'],
      }
    }
    const payloads = (queried.rows ?? []).map((row) => {
      const value = row.f?.[0]?.v
      if (!value) throw new Error('MCP_PROPERTY_REFERENCE_ROW_INVALID')
      return JSON.parse(value) as PropertyReferencePayload
    })
    if (!payloads.length) {
      return {
        ...base(scope, now),
        status: 'NOT_FOUND',
        data: [],
        missing_fields: ['regional_property_reference'],
        source_trace: [],
      }
    }

    const sourceTrace = new Map<string, Record<string, unknown>>()
    const data = payloads.map((payload) => {
      const dataDate = periodEnd(payload.period_code)
      const combinedDigest = digestPair(payload)
      const id = evidenceId(payload)
      const rentRef = page(payload.rent_table_id)
      const conversionRef = page(payload.conversion_table_id)
      const rawDigests = JSON.parse(payload.source_digests_json) as Record<string, string>
      for (const [tableId, sourceRef] of [
        [payload.rent_table_id, rentRef],
        [payload.conversion_table_id, conversionRef],
      ] as const) {
        sourceTrace.set(tableId, {
          source_id: `reb-commercial-rent:${tableId}`,
          source_ref: sourceRef,
          data_date: dataDate,
          retrieved_at: now.toISOString(),
          content_digest: `sha256:${rawDigests[tableId]}`,
        })
      }
      return {
        property_class: payload.property_class,
        effective_rent_krw_per_sqm_month: payload.effective_rent_krw_per_sqm_month,
        conversion_rate_bps: payload.conversion_rate_bps,
        period: payload.period_code,
        region_code: payload.region_code,
        region_name: payload.region_name,
        coverage_status: payload.coverage_status,
        floor_basis: payload.floor_basis,
        evidence_id: id,
        _evidence: {
          schema_version: '2.0.0',
          evidence_id: id,
          project_id: scope.ventureProjectId,
          claim_type: 'PROPERTY_RENT_REFERENCE',
          metric: 'EFFECTIVE_RENT_AND_CONVERSION_RATE',
          value: {
            kind: 'STRING',
            value: JSON.stringify({
              effective_rent_krw_per_sqm_month: payload.effective_rent_krw_per_sqm_month,
              conversion_rate_bps: payload.conversion_rate_bps,
              property_class: payload.property_class,
            }),
          },
          value_kind: 'EVIDENCED_FACT',
          unit: 'REB_EFFECTIVE_RENT_AND_CONVERSION',
          geographic_scope: {
            scope_type: 'REGION',
            scope_id: payload.region_code,
            boundary_version: null,
          },
          source: {
            title: '한국부동산원 상업용부동산 임대동향조사',
            source_ref: rentRef,
            authority: 'PRIMARY_DATA',
            source_type: 'DATASET',
            published_or_data_date: dataDate,
            source_observed_at: now.toISOString(),
            document_version: payload.ingestion_id,
            checksum: `sha256:${combinedDigest}`,
          },
          original_anchor: {
            anchor_type: 'CALCULATION',
            locator: `${payload.period_code}:${payload.region_code}:${payload.property_class}`,
            excerpt_hash: `sha256:${combinedDigest}`,
          },
          freshness_status: Date.parse(input.as_of) - Date.parse(dataDate) <= 365 * 86400 * 1000
            ? 'FRESH'
            : 'STALE',
          conflict_status: 'NONE',
          retrieved_at: now.toISOString(),
          missing_context: [
            'PARENT_REGION_BENCHMARK_NOT_ACTUAL_LISTING',
            'FIRST_FLOOR_EFFECTIVE_RENT_BASIS',
            'MANAGEMENT_FEE_EXCLUDED_FROM_SOURCE_RENT',
          ],
          durable_evidence_refs: [rentRef, conversionRef],
        },
      }
    })
    const evidenceRecords = data.map((value) => value._evidence)
    const publicData = data.map(({ _evidence, ...value }) => value)
    return {
      ...base(scope, now),
      status: 'OK',
      data: publicData,
      evidence_records: evidenceRecords,
      missing_fields: [],
      source_trace: [...sourceTrace.values()],
    }
  }
}