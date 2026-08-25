import { createHash } from 'node:crypto'
import { getFranchiseResearchSnapshot } from './franchise-catalog'
import type { McpConnector, McpScopeContext } from './router'

const BIGQUERY_API = 'https://bigquery.googleapis.com/bigquery/v2'
const QUERY_TIMEOUT_MS = 10_000
const BRAND_SOURCE_REF = 'https://www.data.go.kr/data/15125467/openapi.do'
const STARTUP_COST_SOURCE_REF = 'https://www.data.go.kr/data/15110265/openapi.do'

interface FranchiseDisclosureOptions {
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

interface DisclosurePayload {
  ingestion_id: string
  reporting_year: number
  brand_management_no: string
  headquarters_management_no: string
  brand_name: string
  field: string
  value_krw: number
  source_field: string
  source_digests_json: string
  identity_count?: number
}

const QUERY = `
WITH latest AS (
  SELECT ingestion_id, reporting_year, source_digests_json
  FROM \`__PROJECT__.__DATASET__.franchise_disclosure_manifest\`
  WHERE status = 'APPROVED'
    AND reporting_year <= EXTRACT(YEAR FROM @as_of)
  ORDER BY reporting_year DESC, loaded_at DESC
  LIMIT 1
), identities AS (
  SELECT registry.*,
         COUNT(DISTINCT registry.brand_management_no) OVER () AS identity_count
  FROM \`__PROJECT__.__DATASET__.franchise_brand_registry\` AS registry
  JOIN latest USING (ingestion_id, reporting_year)
  WHERE registry.brand_name = @brand_name
    AND registry.industry_major = '외식'
    AND registry.industry_middle = '커피'
)
SELECT TO_JSON_STRING(STRUCT(
  fact.ingestion_id,
  fact.reporting_year,
  fact.brand_management_no,
  fact.headquarters_management_no,
  fact.brand_name,
  fact.field,
  fact.value_krw,
  fact.source_field,
  latest.source_digests_json,
  identities.identity_count
)) AS payload
FROM \`__PROJECT__.__DATASET__.franchise_disclosure_fact\` AS fact
JOIN latest USING (ingestion_id, reporting_year)
JOIN identities USING (ingestion_id, reporting_year, brand_management_no, headquarters_management_no, brand_name)
ORDER BY fact.field
`

const FIELD_ORDER = [
  'FRANCHISE_FEE',
  'EDUCATION_FEE',
  'FRANCHISEE_DEPOSIT',
  'OTHER_INITIAL_FEE',
  'FRANCHISE_INITIAL_FEE_TOTAL',
] as const

function validateIdentifier(value: string, label: string): string {
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(value)) {
    throw new Error(`MCP_FRANCHISE_DISCLOSURE_${label}_INVALID`)
  }
  return value
}

function evidenceId(payload: DisclosurePayload): string {
  const identity = [
    payload.ingestion_id,
    payload.reporting_year,
    payload.brand_management_no,
    'startup-cost-schedule',
  ].join(':')
  return `ftc-franchise:${createHash('sha256').update(identity).digest('hex')}`
}

function sourceDigest(payload: DisclosurePayload): string {
  const parsed = JSON.parse(payload.source_digests_json) as Record<string, unknown>
  const brand = parsed.brand_registry
  const startup = parsed.startup_cost
  if (typeof brand !== 'string' || !/^[0-9a-f]{64}$/.test(brand)
    || typeof startup !== 'string' || !/^[0-9a-f]{64}$/.test(startup)) {
    throw new Error('MCP_FRANCHISE_DISCLOSURE_DIGEST_INVALID')
  }
  return createHash('sha256').update(`${brand}:${startup}`).digest('hex')
}

function base(scope: McpScopeContext, now: Date) {
  return {
    schema_version: '1.0.0',
    request_id: scope.requestId,
    tool_name: 'get_franchise_disclosure',
    tool_version: '1.0.0',
    project_id: scope.ventureProjectId,
    evidence_records: [],
    conflicts: [],
    error_codes: [],
    observed_at: now.toISOString(),
  }
}

function displayNameForBrand(brandId: string): string | null {
  const snapshot = getFranchiseResearchSnapshot()
  const brand = snapshot.catalog.brands.find((value) => value.brand_id === brandId)
  return brand?.display_name ?? null
}

export function createFranchiseDisclosureConnector(options: FranchiseDisclosureOptions): McpConnector {
  const projectId = validateIdentifier(options.projectId, 'PROJECT')
  const datasetId = validateIdentifier(options.datasetId ?? 'caffemate_grounding', 'DATASET')
  const location = options.location ?? 'asia-northeast3'
  if (location !== 'asia-northeast3') throw new Error('MCP_FRANCHISE_DISCLOSURE_LOCATION_INVALID')
  const fetcher = options.fetch ?? globalThis.fetch
  const clock = options.now ?? (() => new Date())

  return async (rawInput, scope, execution) => {
    const input = rawInput as { brand_id: string; disclosure_version?: string; as_of: string }
    const now = clock()
    const brandName = displayNameForBrand(input.brand_id)
    if (!brandName) {
      return {
        ...base(scope, now),
        status: 'NOT_FOUND',
        data: [],
        missing_fields: ['franchise_disclosure'],
        source_trace: [],
      }
    }
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
              name: 'brand_name',
              parameterType: { type: 'STRING' },
              parameterValue: { value: brandName },
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
        missing_fields: ['franchise_disclosure'],
        source_trace: [],
        error_codes: ['FRANCHISE_DISCLOSURE_QUERY_FAILED'],
      }
    }
    const queried = await response.json() as QueryResponse
    if (queried.errors?.length || queried.jobComplete === false) {
      return {
        ...base(scope, now),
        status: 'ERROR',
        data: [],
        missing_fields: ['franchise_disclosure'],
        source_trace: [],
        error_codes: ['FRANCHISE_DISCLOSURE_QUERY_FAILED'],
      }
    }
    const payloads = (queried.rows ?? []).map((row) => {
      const value = row.f?.[0]?.v
      if (!value) throw new Error('MCP_FRANCHISE_DISCLOSURE_ROW_INVALID')
      return JSON.parse(value) as DisclosurePayload
    })
    if (!payloads.length) {
      return {
        ...base(scope, now),
        status: 'NOT_FOUND',
        data: [],
        missing_fields: ['franchise_disclosure'],
        source_trace: [],
      }
    }
    if (payloads.some((payload) => Number(payload.identity_count ?? 1) !== 1)) {
      return {
        ...base(scope, now),
        status: 'ERROR',
        data: [],
        missing_fields: ['franchise_disclosure'],
        source_trace: [],
        error_codes: ['FRANCHISE_DISCLOSURE_IDENTITY_AMBIGUOUS'],
      }
    }
    const identity = payloads[0]
    if (payloads.some((payload) => payload.reporting_year !== identity.reporting_year
      || payload.brand_management_no !== identity.brand_management_no
      || payload.headquarters_management_no !== identity.headquarters_management_no
      || payload.brand_name !== identity.brand_name)) {
      return {
        ...base(scope, now),
        status: 'ERROR',
        data: [],
        missing_fields: ['franchise_disclosure'],
        source_trace: [],
        error_codes: ['FRANCHISE_DISCLOSURE_IDENTITY_MIXED'],
      }
    }
    const requestedVersion = input.disclosure_version
    if (requestedVersion) {
      return {
        ...base(scope, now),
        status: 'NOT_FOUND',
        data: [],
        missing_fields: ['franchise_disclosure_document_identity'],
        source_trace: [],
      }
    }
    // The currently approved FTC startup-cost source is versioned by reporting
    // year, not by the legal disclosure-document registration number. Keep that
    // distinction explicit so registration recency is never fabricated.
    const sourceVersion = `FTC_COST_REPORTING_YEAR:${identity.reporting_year}:${identity.brand_management_no}`
    const dataDate = `${identity.reporting_year}-12-31`
    const scheduleEvidenceId = evidenceId(identity)
    const data = payloads
      .sort((left, right) => {
        const leftIndex = FIELD_ORDER.indexOf(left.field as typeof FIELD_ORDER[number])
        const rightIndex = FIELD_ORDER.indexOf(right.field as typeof FIELD_ORDER[number])
        return leftIndex - rightIndex
      })
      .map((payload) => {
        return {
          brand_id: input.brand_id,
          brand_name: payload.brand_name,
          ftc_brand_management_no: payload.brand_management_no,
          ftc_headquarters_management_no: payload.headquarters_management_no,
          source_version: sourceVersion,
          disclosure_version: null,
          disclosure_registration_date: null,
          reporting_year: payload.reporting_year,
          field: payload.field,
          value: { kind: 'INTEGER', value: payload.value_krw },
          unit: 'KRW',
          effective_date: dataDate,
          evidence_id: scheduleEvidenceId,
        }
      })
    const amounts = new Map(data.map((value) => [value.field, value.value.value]))
    const complete = FIELD_ORDER.every((field) => amounts.has(field))
    if (complete) {
      const componentTotal = ['FRANCHISE_FEE', 'EDUCATION_FEE', 'FRANCHISEE_DEPOSIT', 'OTHER_INITIAL_FEE']
        .reduce((sum, field) => sum + Number(amounts.get(field) ?? 0), 0)
      if (componentTotal !== amounts.get('FRANCHISE_INITIAL_FEE_TOTAL')) {
        return {
          ...base(scope, now),
          status: 'ERROR',
          data: [],
          missing_fields: ['franchise_disclosure'],
          source_trace: [],
          error_codes: ['FRANCHISE_DISCLOSURE_TOTAL_MISMATCH'],
        }
      }
    }
    const digest = sourceDigest(identity)
    const evidenceRecords = [{
      schema_version: '2.0.0',
      evidence_id: scheduleEvidenceId,
      project_id: scope.ventureProjectId,
      claim_type: 'FRANCHISE_DISCLOSURE_FACT',
      metric: input.brand_id,
      value: {
        kind: 'STRING',
        value: JSON.stringify(Object.fromEntries(data.map((value) => [value.field, value.value.value]))),
      },
      value_kind: 'EVIDENCED_FACT',
      unit: 'KRW',
      geographic_scope: {
        scope_type: 'NATIONAL',
        scope_id: 'KR',
        boundary_version: null,
      },
      source: {
        title: '공정거래위원회 브랜드별 창업 금액 현황',
        source_ref: STARTUP_COST_SOURCE_REF,
        authority: 'PRIMARY_DATA',
        source_type: 'DATASET',
        published_or_data_date: dataDate,
        source_observed_at: now.toISOString(),
        document_version: sourceVersion,
        checksum: `sha256:${digest}`,
      },
      original_anchor: {
        anchor_type: 'CALCULATION',
        locator: `${identity.reporting_year}:${identity.brand_management_no}:startup-cost-schedule`,
        excerpt_hash: `sha256:${digest}`,
      },
      freshness_status: 'FRESH',
      conflict_status: 'NONE',
      retrieved_at: now.toISOString(),
      missing_context: [
        'FTC_REGISTRATION_DOES_NOT_PROVE_CURRENT_RECRUITMENT',
        'HQ_AREA_APPROVAL_NOT_PROVIDED',
        'REPORTING_YEAR_SNAPSHOT_NOT_DISCLOSURE_DOCUMENT_VERSION',
      ],
      durable_evidence_refs: [BRAND_SOURCE_REF, STARTUP_COST_SOURCE_REF],
    }]
    const sourceDigests = JSON.parse(payloads[0].source_digests_json) as Record<string, string>
    return {
      ...base(scope, now),
      status: 'PARTIAL',
      data,
      evidence_records: evidenceRecords,
      missing_fields: complete
        ? ['franchise_disclosure_document_identity']
        : ['franchise_disclosure_document_identity', 'franchise_initial_fee_components'],
      source_trace: [
        {
          source_id: 'ftc-franchise-brand-registry',
          source_ref: BRAND_SOURCE_REF,
          data_date: dataDate,
          retrieved_at: now.toISOString(),
          content_digest: `sha256:${sourceDigests.brand_registry}`,
        },
        {
          source_id: 'ftc-franchise-startup-cost',
          source_ref: STARTUP_COST_SOURCE_REF,
          data_date: dataDate,
          retrieved_at: now.toISOString(),
          content_digest: `sha256:${sourceDigests.startup_cost}`,
        },
      ],
    }
  }
}