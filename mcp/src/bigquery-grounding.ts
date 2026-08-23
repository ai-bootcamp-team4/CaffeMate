import { createHash } from 'node:crypto'
import type { McpConnector, McpScopeContext } from './router'

const BIGQUERY_API = 'https://bigquery.googleapis.com/bigquery/v2'
const QUERY_TIMEOUT_MS = 10_000
const QUERY_POLL_ATTEMPTS = 3

const SOURCE = {
  mapping: {
    id: 'mois-admin-legal-mapping',
    ref: 'https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000052&nttId=127039',
  },
  store: {
    id: 'seoul-cafe-store-quarterly',
    ref: 'https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do',
  },
  sales: {
    id: 'seoul-cafe-sales-quarterly',
    ref: 'https://data.seoul.go.kr/dataList/OA-15572/A/1/datasetView.do',
  },
  foot: {
    id: 'seoul-foot-traffic-quarterly',
    ref: 'https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do?tab=A',
  },
  resident: {
    id: 'seoul-resident-population-quarterly',
    ref: 'https://data.seoul.go.kr/dataList/OA-22182/S/1/datasetView.do?tab=A',
  },
  worker: {
    id: 'seoul-worker-population-quarterly',
    ref: 'https://data.seoul.go.kr/dataList/OA-22184/A/1/datasetView.do',
  },
} as const

type SourceKind = keyof typeof SOURCE

interface BigQueryGroundingOptions {
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
  jobReference?: { jobId?: string }
  rows?: QueryRow[]
  errors?: Array<{ reason?: string }>
}

interface GroundingPayload {
  ingestion_id: string
  loaded_at: string
  source_periods_json: string
  source_digests_json: string
  mapping_revision: string | null
  mapped_admin_codes: string[]
  mapped_admin_names: string[]
  store_count: number | null
  franchise_store_count: number | null
  open_count: number | null
  close_count: number | null
  closure_rate: number | null
  estimated_sales_krw: number | null
  estimated_sales_count: number | null
  foot_traffic: number | null
  resident_population: number | null
  worker_population: number | null
  resident_age_10: number | null
  resident_age_20: number | null
  resident_age_30: number | null
  resident_age_40: number | null
  resident_age_50: number | null
  resident_age_60_plus: number | null
}

interface MetricRecord {
  metric: string
  value: { kind: 'INTEGER'; value: number } | { kind: 'DECIMAL'; value: number } | { kind: 'STRING'; value: string }
  unit: string | null
  as_of: string
  evidence_id: string
}

const GROUNDING_QUERY = `
WITH latest AS (
  SELECT ingestion_id, loaded_at, source_periods_json, source_digests_json
  FROM \`__PROJECT__.__DATASET__.source_manifest\`
  WHERE status = 'APPROVED'
  ORDER BY loaded_at DESC
  LIMIT 1
),
mapped AS (
  SELECT DISTINCT m.admin_dong_code, m.admin_dong_name, m.source_revision
  FROM \`__PROJECT__.__DATASET__.area_mapping\` AS m
  CROSS JOIN latest AS l
  WHERE m.ingestion_id = l.ingestion_id
    AND m.deleted_date IS NULL
    AND (
      (LENGTH(@administrative_code) = 10 AND m.legal_dong_code = @administrative_code)
      OR (LENGTH(@administrative_code) = 8 AND m.admin_dong_code = @administrative_code)
    )
),
store AS (
  SELECT
    SUM(f.store_count) AS store_count,
    SUM(f.franchise_store_count) AS franchise_store_count,
    SUM(f.open_count) AS open_count,
    SUM(f.closure_count) AS close_count,
    SAFE_MULTIPLY(SAFE_DIVIDE(SUM(f.closure_count), SUM(f.store_count)), 100) AS closure_rate
  FROM \`__PROJECT__.__DATASET__.seoul_cafe_store_fact\` AS f
  JOIN mapped USING (admin_dong_code)
  CROSS JOIN latest AS l
  WHERE f.ingestion_id = l.ingestion_id
),
sales AS (
  SELECT
    SUM(f.estimated_sales_krw) AS estimated_sales_krw,
    SUM(f.estimated_sales_count) AS estimated_sales_count
  FROM \`__PROJECT__.__DATASET__.seoul_cafe_sales_fact\` AS f
  JOIN mapped USING (admin_dong_code)
  CROSS JOIN latest AS l
  WHERE f.ingestion_id = l.ingestion_id
),
population AS (
  SELECT
    SUM(IF(f.population_kind = 'FOOT_TRAFFIC', f.total_count, NULL)) AS foot_traffic,
    SUM(IF(f.population_kind = 'RESIDENT', f.total_count, NULL)) AS resident_population,
    SUM(IF(f.population_kind = 'WORKER', f.total_count, NULL)) AS worker_population,
    SUM(IF(f.population_kind = 'RESIDENT', f.age_10_count, NULL)) AS resident_age_10,
    SUM(IF(f.population_kind = 'RESIDENT', f.age_20_count, NULL)) AS resident_age_20,
    SUM(IF(f.population_kind = 'RESIDENT', f.age_30_count, NULL)) AS resident_age_30,
    SUM(IF(f.population_kind = 'RESIDENT', f.age_40_count, NULL)) AS resident_age_40,
    SUM(IF(f.population_kind = 'RESIDENT', f.age_50_count, NULL)) AS resident_age_50,
    SUM(IF(f.population_kind = 'RESIDENT', f.age_60_plus_count, NULL)) AS resident_age_60_plus
  FROM \`__PROJECT__.__DATASET__.seoul_population_fact\` AS f
  JOIN mapped USING (admin_dong_code)
  CROSS JOIN latest AS l
  WHERE f.ingestion_id = l.ingestion_id
)
SELECT TO_JSON_STRING(STRUCT(
  l.ingestion_id,
  CAST(l.loaded_at AS STRING) AS loaded_at,
  l.source_periods_json,
  l.source_digests_json,
  (SELECT ANY_VALUE(source_revision) FROM mapped) AS mapping_revision,
  ARRAY(SELECT admin_dong_code FROM mapped ORDER BY admin_dong_code) AS mapped_admin_codes,
  ARRAY(SELECT admin_dong_name FROM mapped ORDER BY admin_dong_name) AS mapped_admin_names,
  s.store_count,
  s.franchise_store_count,
  s.open_count,
  s.close_count,
  s.closure_rate,
  x.estimated_sales_krw,
  x.estimated_sales_count,
  p.foot_traffic,
  p.resident_population,
  p.worker_population,
  p.resident_age_10,
  p.resident_age_20,
  p.resident_age_30,
  p.resident_age_40,
  p.resident_age_50,
  p.resident_age_60_plus
)) AS payload
FROM latest AS l
CROSS JOIN store AS s
CROSS JOIN sales AS x
CROSS JOIN population AS p
`

function validateIdentifier(value: string, label: string): string {
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(value)) throw new Error(`MCP_BIGQUERY_${label}_INVALID`)
  return value
}

function base(scope: McpScopeContext, toolName: string, now: Date) {
  return {
    schema_version: '1.0.0',
    request_id: scope.requestId,
    tool_name: toolName,
    tool_version: '1.0.0',
    project_id: scope.ventureProjectId,
    evidence_records: [],
    conflicts: [],
    error_codes: [],
    observed_at: now.toISOString(),
  }
}

function periodEnd(period: string | undefined): string | null {
  if (!period || !/^\d{4}[1-4]$/.test(period)) return null
  const year = Number(period.slice(0, 4))
  const quarter = Number(period[4])
  const month = quarter * 3
  return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10)
}

function metric(
  payload: GroundingPayload,
  source: SourceKind,
  name: string,
  value: number | string,
  unit: string | null,
  dataDate: string,
  kind: 'INTEGER' | 'DECIMAL' | 'STRING',
): MetricRecord {
  const identity = `${payload.ingestion_id}:${name}:${payload.mapped_admin_codes.join(',')}`
  return {
    metric: name,
    value: kind === 'INTEGER'
      ? { kind, value: Math.round(Number(value)) }
      : kind === 'DECIMAL'
        ? { kind, value: Number(value) }
        : { kind, value: String(value) },
    unit,
    as_of: dataDate,
    evidence_id: `${SOURCE[source].id}:${payload.ingestion_id}:${createHash('sha256').update(identity).digest('hex')}`,
  }
}

function sourceTrace(
  payload: GroundingPayload,
  source: SourceKind,
  periods: Record<string, string>,
  digests: Record<string, string>,
  retrievedAt: string,
) {
  const digestKey = source === 'mapping' ? 'mapping_zip' : source
  const sourceDigest = digests[digestKey]
  if (!sourceDigest || !/^[0-9a-f]{64}$/.test(sourceDigest)) return null
  const dataDate = source === 'mapping'
    ? payload.mapping_revision?.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3') ?? null
    : periodEnd(periods[source])
  return {
    source_id: SOURCE[source].id,
    source_ref: SOURCE[source].ref,
    data_date: dataDate,
    retrieved_at: retrievedAt,
    content_digest: `sha256:${sourceDigest}`,
  }
}

class BigQueryGroundingClient {
  private readonly projectId: string
  private readonly datasetId: string
  private readonly location: string
  private readonly accessToken: () => Promise<string>
  private readonly fetcher: typeof globalThis.fetch

  constructor(options: BigQueryGroundingOptions) {
    this.projectId = validateIdentifier(options.projectId, 'PROJECT')
    this.datasetId = validateIdentifier(options.datasetId ?? 'caffemate_grounding', 'DATASET')
    this.location = options.location ?? 'asia-northeast3'
    if (this.location !== 'asia-northeast3') throw new Error('MCP_BIGQUERY_LOCATION_INVALID')
    this.accessToken = options.accessToken
    this.fetcher = options.fetch ?? globalThis.fetch
  }

  async area(administrativeCode: string, signal?: AbortSignal): Promise<GroundingPayload | null> {
    const token = await this.accessToken()
    const query = GROUNDING_QUERY
      .replaceAll('__PROJECT__', this.projectId)
      .replaceAll('__DATASET__', this.datasetId)
    const response = await this.request<QueryResponse>(
      `${BIGQUERY_API}/projects/${this.projectId}/queries`,
      token,
      {
        method: 'POST',
        body: JSON.stringify({
          query,
          useLegacySql: false,
          location: this.location,
          timeoutMs: QUERY_TIMEOUT_MS,
          parameterMode: 'NAMED',
          queryParameters: [{
            name: 'administrative_code',
            parameterType: { type: 'STRING' },
            parameterValue: { value: administrativeCode },
          }],
        }),
        signal,
      },
    )
    const complete = response.jobComplete
      ? response
      : await this.poll(response.jobReference?.jobId, token, signal)
    if (complete.errors?.length) {
      throw new Error(`MCP_BIGQUERY_QUERY_FAILED:${complete.errors[0]?.reason ?? 'UNKNOWN'}`)
    }
    const value = complete.rows?.[0]?.f?.[0]?.v
    if (!value) return null
    const parsed = JSON.parse(value) as GroundingPayload
    if (!parsed.ingestion_id || !Array.isArray(parsed.mapped_admin_codes)) {
      throw new Error('MCP_BIGQUERY_PAYLOAD_INVALID')
    }
    return parsed
  }

  private async poll(jobId: string | undefined, token: string, signal?: AbortSignal): Promise<QueryResponse> {
    if (!jobId) throw new Error('MCP_BIGQUERY_JOB_ID_MISSING')
    for (let attempt = 0; attempt < QUERY_POLL_ATTEMPTS; attempt += 1) {
      const url = new URL(`${BIGQUERY_API}/projects/${this.projectId}/queries/${jobId}`)
      url.searchParams.set('location', this.location)
      url.searchParams.set('timeoutMs', String(QUERY_TIMEOUT_MS))
      const response = await this.request<QueryResponse>(url.toString(), token, { signal })
      if (response.jobComplete) return response
    }
    throw new Error('MCP_BIGQUERY_QUERY_TIMED_OUT')
  }

  private async request<T>(url: string, token: string, init: RequestInit): Promise<T> {
    const response = await this.fetcher(url, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'X-Goog-User-Project': this.projectId,
      },
    })
    if (!response.ok) throw new Error(`MCP_BIGQUERY_HTTP_${response.status}`)
    return await response.json() as T
  }
}

export function createBigQueryGroundingConnectors(
  options: BigQueryGroundingOptions,
): Pick<Record<'get_area_profile' | 'search_cafe_observations', McpConnector>, 'get_area_profile' | 'search_cafe_observations'> {
  const client = new BigQueryGroundingClient(options)
  const clock = options.now ?? (() => new Date())

  const query = async (rawInput: unknown, scope: McpScopeContext, signal?: AbortSignal) => {
    const input = rawInput as { administrative_code: string; metrics?: string[] }
    const now = clock()
    let payload: GroundingPayload | null
    try {
      payload = await client.area(input.administrative_code, signal)
    } catch {
      return {
        payload: null,
        result: {
          ...base(scope, input.metrics ? 'search_cafe_observations' : 'get_area_profile', now),
          status: 'ERROR',
          data: [],
          missing_fields: ['approved_grounding_snapshot'],
          source_trace: [],
          error_codes: ['GROUNDING_QUERY_FAILED'],
        },
      }
    }
    if (!payload || !payload.mapped_admin_codes.length) {
      return {
        payload,
        result: {
          ...base(scope, input.metrics ? 'search_cafe_observations' : 'get_area_profile', now),
          status: 'NOT_FOUND',
          data: [],
          missing_fields: ['administrative_dong_mapping'],
          source_trace: [],
        },
      }
    }
    return { payload, result: null }
  }

  const areaProfile: McpConnector = async (rawInput, scope, execution) => {
    const queried = await query(rawInput, scope, execution?.signal)
    if (!queried.payload || queried.result) return queried.result
    const payload = queried.payload
    const now = clock()
    const periods = JSON.parse(payload.source_periods_json) as Record<string, string>
    const digests = JSON.parse(payload.source_digests_json) as Record<string, string>
    const data: MetricRecord[] = []
    const mappingDate = sourceTrace(payload, 'mapping', periods, digests, now.toISOString())?.data_date
    if (mappingDate) {
      data.push(metric(payload, 'mapping', 'MAPPED_ADMIN_DONG_COUNT', payload.mapped_admin_codes.length, 'COUNT', mappingDate, 'INTEGER'))
      data.push(metric(payload, 'mapping', 'MAPPED_ADMIN_DONG_NAMES', payload.mapped_admin_names.join(', '), null, mappingDate, 'STRING'))
    }
    const residentDate = periodEnd(periods.resident)
    if (payload.resident_population !== null && residentDate) {
      data.push(metric(payload, 'resident', 'RESIDENT_POPULATION', payload.resident_population, 'PERSONS', residentDate, 'INTEGER'))
    }
    const workerDate = periodEnd(periods.worker)
    if (payload.worker_population !== null && workerDate) {
      data.push(metric(payload, 'worker', 'WORKER_POPULATION', payload.worker_population, 'PERSONS', workerDate, 'INTEGER'))
    }
    const usedSources: SourceKind[] = ['mapping', 'resident', 'worker']
    const traces = usedSources
      .map((source) => sourceTrace(payload, source, periods, digests, now.toISOString()))
      .filter((value) => value !== null)
    return {
      ...base(scope, 'get_area_profile', now),
      status: data.length ? 'OK' : 'PARTIAL',
      data,
      missing_fields: data.length ? [] : ['area_profile_metrics'],
      source_trace: traces,
    }
  }

  const cafeObservations: McpConnector = async (rawInput, scope, execution) => {
    const input = rawInput as { administrative_code: string; metrics: string[] }
    const queried = await query(input, scope, execution?.signal)
    if (!queried.payload || queried.result) return queried.result
    const payload = queried.payload
    const now = clock()
    const periods = JSON.parse(payload.source_periods_json) as Record<string, string>
    const digests = JSON.parse(payload.source_digests_json) as Record<string, string>
    const data: MetricRecord[] = []
    const missing: string[] = []
    const usedSources = new Set<SourceKind>()
    const addNumber = (name: string, value: number | null, source: SourceKind, unit: string, kind: 'INTEGER' | 'DECIMAL' = 'INTEGER') => {
      const dataDate = periodEnd(periods[source])
      if (value === null || !dataDate) {
        missing.push(name)
        return
      }
      data.push(metric(payload, source, name, value, unit, dataDate, kind))
      usedSources.add(source)
    }
    for (const name of input.metrics) {
      if (name === 'CAFE_COUNT') addNumber(name, payload.store_count, 'store', 'STORES')
      else if (name === 'OPEN_COUNT') addNumber(name, payload.open_count, 'store', 'STORES_PER_QUARTER')
      else if (name === 'CLOSE_COUNT') addNumber(name, payload.close_count, 'store', 'STORES_PER_QUARTER')
      else if (name === 'CLOSURE_RATE') addNumber(name, payload.closure_rate, 'store', 'PERCENT_DERIVED', 'DECIMAL')
      else if (name === 'ESTIMATED_SALES') addNumber(name, payload.estimated_sales_krw, 'sales', 'KRW_PER_QUARTER_ESTIMATE')
      else if (name === 'FOOT_TRAFFIC') addNumber(name, payload.foot_traffic, 'foot', 'PERSON_VISITS_PER_QUARTER_ESTIMATE')
      else if (name === 'RESIDENT_POPULATION') addNumber(name, payload.resident_population, 'resident', 'PERSONS')
      else if (name === 'WORKER_POPULATION') addNumber(name, payload.worker_population, 'worker', 'PERSONS')
      else if (name === 'AGE_DISTRIBUTION') {
        const values = {
          age_10: payload.resident_age_10,
          age_20: payload.resident_age_20,
          age_30: payload.resident_age_30,
          age_40: payload.resident_age_40,
          age_50: payload.resident_age_50,
          age_60_plus: payload.resident_age_60_plus,
        }
        const dataDate = periodEnd(periods.resident)
        if (!dataDate || Object.values(values).some((value) => value === null)) missing.push(name)
        else {
          data.push(metric(payload, 'resident', name, JSON.stringify(values), 'RESIDENT_COUNT_BY_AGE_BAND_JSON', dataDate, 'STRING'))
          usedSources.add('resident')
        }
      } else missing.push(name)
    }
    const traces = [...usedSources]
      .map((source) => sourceTrace(payload, source, periods, digests, now.toISOString()))
      .filter((value) => value !== null)
    return {
      ...base(scope, 'search_cafe_observations', now),
      status: data.length && !missing.length ? 'OK' : 'PARTIAL',
      data,
      missing_fields: missing,
      source_trace: traces,
    }
  }

  return {
    get_area_profile: areaProfile,
    search_cafe_observations: cafeObservations,
  }
}

export { BigQueryGroundingClient, periodEnd }
