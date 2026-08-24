import { describe, expect, it, vi } from 'vitest'
import { createBigQueryGroundingConnectors, periodEnd } from '../src/bigquery-grounding'
import { McpToolRouter } from '../src/router'

const projectId = 'proj-aj20-211200020328'
const scope = { ventureProjectId: 'venture-1', workflowRunId: 'workflow-1', requestId: 'request-1' }
const digest = 'a'.repeat(64)

function payload(overrides: Record<string, unknown> = {}) {
  return {
    ingestion_id: '5b206b15c98303940cebfbfa',
    loaded_at: '2026-08-23 02:33:01+00',
    source_periods_json: JSON.stringify({
      store: '20261', sales: '20261', foot: '20261', resident: '20261', worker: '20261',
    }),
    source_digests_json: JSON.stringify({
      store: digest,
      sales: digest,
      foot: digest,
      resident: digest,
      worker: digest,
      mapping_zip: digest,
    }),
    mapping_revision: '20260701',
    mapped_admin_codes: ['11440690', '11440700'],
    mapped_admin_names: ['망원제1동', '망원제2동'],
    store_count: 73,
    franchise_store_count: 14,
    open_count: 4,
    close_count: 6,
    closure_rate: 8.219,
    estimated_sales_krw: 1_234_567_890,
    estimated_sales_count: 45_678,
    foot_traffic: 2_000_000,
    resident_population: 40_000,
    worker_population: 22_000,
    resident_age_10: 5_000,
    resident_age_20: 6_000,
    resident_age_30: 7_000,
    resident_age_40: 8_000,
    resident_age_50: 7_000,
    resident_age_60_plus: 7_000,
    ...overrides,
  }
}

function queryFetcher(value: Record<string, unknown>, administrativeCode = '1144012300') {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    expect(String(input)).toBe(`${'https://bigquery.googleapis.com/bigquery/v2'}/projects/${projectId}/queries`)
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer access-token',
      'X-Goog-User-Project': projectId,
    })
    const body = JSON.parse(String(init?.body))
    expect(body.location).toBe('asia-northeast3')
    expect(body.queryParameters).toEqual([{
      name: 'administrative_code',
      parameterType: { type: 'STRING' },
      parameterValue: { value: administrativeCode },
    }])
    expect(body.query).toContain('source_manifest')
    return Response.json({
      jobComplete: true,
      rows: [{ f: [{ v: JSON.stringify(value) }] }],
    })
  }) as typeof fetch
}

describe('BigQuery grounding connectors', () => {
  it('returns mapped area profile metrics with official source traces', async () => {
    const fetcher = queryFetcher(payload())
    const connectors = createBigQueryGroundingConnectors({
      projectId,
      accessToken: async () => 'access-token',
      fetch: fetcher,
      now: () => new Date('2026-08-23T03:00:00Z'),
    })
    const result = await new McpToolRouter(connectors).call('get_area_profile', {
      administrative_code: '1144012300',
      boundary_version: 'MOIS_LEGAL_DONG_20260301',
      as_of: '2026-08-23',
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'OK',
      tool_name: 'get_area_profile',
      project_id: 'venture-1',
      data: [
        { metric: 'MAPPED_ADMIN_DONG_COUNT', value: { kind: 'INTEGER', value: 2 }, as_of: '2026-07-01' },
        { metric: 'MAPPED_ADMIN_DONG_NAMES', value: { kind: 'STRING', value: '망원제1동, 망원제2동' } },
        { metric: 'RESIDENT_POPULATION', value: { kind: 'INTEGER', value: 40000 }, as_of: '2026-03-31' },
        { metric: 'WORKER_POPULATION', value: { kind: 'INTEGER', value: 22000 }, as_of: '2026-03-31' },
      ],
    })
    expect(result.source_trace).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source_id: 'mois-admin-legal-mapping',
        data_date: '2026-07-01',
        content_digest: `sha256:${digest}`,
      }),
      expect.objectContaining({ source_id: 'seoul-resident-population-quarterly' }),
    ]))
  })

  it('returns requested cafe metrics and keeps unavailable consumption explicit', async () => {
    const connectors = createBigQueryGroundingConnectors({
      projectId,
      accessToken: async () => 'access-token',
      fetch: queryFetcher(payload()),
      now: () => new Date('2026-08-23T03:00:00Z'),
    })
    const result = await new McpToolRouter(connectors).call('search_cafe_observations', {
      administrative_code: '1144012300',
      boundary_version: 'MOIS_LEGAL_DONG_20260301',
      as_of: '2026-08-23',
      metrics: ['CAFE_COUNT', 'CLOSE_COUNT', 'CLOSURE_RATE', 'ESTIMATED_SALES', 'CONSUMPTION'],
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'PARTIAL',
      missing_fields: ['CONSUMPTION'],
      data: [
        { metric: 'CAFE_COUNT', value: { kind: 'INTEGER', value: 73 }, unit: 'STORES' },
        { metric: 'CLOSE_COUNT', value: { kind: 'INTEGER', value: 6 } },
        { metric: 'CLOSURE_RATE', value: { kind: 'DECIMAL', value: 8.219 }, unit: 'PERCENT_DERIVED' },
        { metric: 'ESTIMATED_SALES', value: { kind: 'INTEGER', value: 1234567890 }, unit: 'KRW_PER_QUARTER_ESTIMATE' },
      ],
      evidence_records: [
        expect.objectContaining({
          project_id: 'venture-1',
          claim_type: 'AREA_CAFE_COMPETITION',
          metric: 'CAFE_COUNT',
          value: { kind: 'INTEGER', value: 73 },
          value_kind: 'EVIDENCED_FACT',
          unit: 'STORES',
          geographic_scope: {
            scope_type: 'ADMINISTRATIVE_AREA',
            scope_id: '1144012300',
            boundary_version: 'MOIS_LEGAL_DONG_20260301',
          },
          freshness_status: 'FRESH',
          conflict_status: 'NONE',
        }),
        expect.objectContaining({
          claim_type: 'AREA_BUSINESS_CHURN',
          metric: 'CLOSE_COUNT',
        }),
        expect.objectContaining({
          claim_type: 'AREA_BUSINESS_CHURN',
          metric: 'CLOSURE_RATE',
          value_kind: 'DERIVED_RESULT',
        }),
        expect.objectContaining({
          claim_type: 'AREA_DEMAND_SIGNALS',
          metric: 'ESTIMATED_SALES',
        }),
      ],
    })
    const data = result.data as Array<Record<string, unknown>>
    expect(data.every((row) => String(row.evidence_id).includes('5b206b15c98303940cebfbfa'))).toBe(true)
  })

  it('does not turn an unmapped non-Seoul area into zero metrics', async () => {
    const connectors = createBigQueryGroundingConnectors({
      projectId,
      accessToken: async () => 'access-token',
      fetch: queryFetcher(
        payload({ mapped_admin_codes: [], mapped_admin_names: [] }),
        '4111113700',
      ),
    })
    const result = await new McpToolRouter(connectors).call('get_area_profile', {
      administrative_code: '4111113700',
      boundary_version: 'MOIS_LEGAL_DONG_20260301',
      as_of: '2026-08-23',
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'NOT_FOUND',
      data: [],
      missing_fields: ['administrative_dong_mapping'],
    })
  })

  it('keeps provider failure explicit instead of returning empty success', async () => {
    const connectors = createBigQueryGroundingConnectors({
      projectId,
      accessToken: async () => 'access-token',
      fetch: vi.fn(async () => new Response('unavailable', { status: 503 })) as typeof fetch,
    })
    const result = await new McpToolRouter(connectors).call('get_area_profile', {
      administrative_code: '1144012300',
      boundary_version: 'MOIS_LEGAL_DONG_20260301',
      as_of: '2026-08-23',
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'ERROR',
      data: [],
      error_codes: ['GROUNDING_QUERY_FAILED'],
    })
  })
})

describe('period end', () => {
  it('uses the actual quarter boundary and rejects invalid period codes', () => {
    expect(periodEnd('20261')).toBe('2026-03-31')
    expect(periodEnd('20264')).toBe('2026-12-31')
    expect(periodEnd('20265')).toBeNull()
  })
})
