import { describe, expect, it, vi } from 'vitest'
import { createFranchiseDisclosureConnector } from '../src/franchise-disclosure'
import { McpToolRouter } from '../src/router'

const projectId = 'proj-aj20-211200020328'
const scope = { ventureProjectId: 'venture-1', workflowRunId: 'workflow-1', requestId: 'request-1' }

describe('FTC franchise disclosure connector', () => {
  it('returns only the exact registered brand and preserves official fee components', async () => {
    const fetcher = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body))
      expect(body.query).toContain('franchise_disclosure_manifest')
      expect(body.query).toContain('franchise_brand_registry')
      expect(body.query).toContain('franchise_disclosure_fact')
      expect(body.queryParameters).toEqual([
        {
          name: 'brand_name',
          parameterType: { type: 'STRING' },
          parameterValue: { value: '이디야커피' },
        },
        {
          name: 'as_of',
          parameterType: { type: 'DATE' },
          parameterValue: { value: '2026-08-25' },
        },
      ])
      const row = (field: string, value: number) => ({ f: [{ v: JSON.stringify({
        ingestion_id: 'ftc-ingestion-1',
        reporting_year: 2024,
        brand_management_no: 'B-EDIYA',
        headquarters_management_no: 'H-EDIYA',
        brand_name: '이디야커피',
        field,
        value_krw: value,
        source_field: field,
        source_digests_json: JSON.stringify({
          brand_registry: 'a'.repeat(64),
          startup_cost: 'b'.repeat(64),
        }),
      }) }] })
      return Response.json({
        jobComplete: true,
        rows: [
          row('FRANCHISE_FEE', 9_900_000),
          row('EDUCATION_FEE', 3_300_000),
          row('FRANCHISEE_DEPOSIT', 5_000_000),
          row('OTHER_INITIAL_FEE', 109_690_000),
          row('FRANCHISE_INITIAL_FEE_TOTAL', 127_890_000),
        ],
      })
    }) as typeof fetch
    const connector = createFranchiseDisclosureConnector({
      projectId,
      accessToken: async () => 'access-token',
      fetch: fetcher,
      now: () => new Date('2026-08-25T06:00:00Z'),
    })

    const result = await new McpToolRouter({ get_franchise_disclosure: connector }).call(
      'get_franchise_disclosure',
      { brand_id: 'kr-ediya-coffee', as_of: '2026-08-25' },
      scope,
    ) as {
      status: string
      data: Array<Record<string, unknown>>
      evidence_records: Array<Record<string, unknown>>
    }

    expect(result.status).toBe('PARTIAL')
    expect(result.data).toHaveLength(5)
    expect(result.data[4]).toMatchObject({
      brand_id: 'kr-ediya-coffee',
      brand_name: '이디야커피',
      ftc_brand_management_no: 'B-EDIYA',
      ftc_headquarters_management_no: 'H-EDIYA',
      source_version: 'FTC_COST_REPORTING_YEAR:2024:B-EDIYA',
      disclosure_version: null,
      disclosure_registration_date: null,
      reporting_year: 2024,
      field: 'FRANCHISE_INITIAL_FEE_TOTAL',
      value: { kind: 'INTEGER', value: 127_890_000 },
      unit: 'KRW',
      effective_date: '2024-12-31',
    })
    expect(new Set(result.data.map((item: Record<string, unknown>) => item.evidence_id)).size).toBe(1)
    expect(result.evidence_records).toHaveLength(1)
    expect(result.evidence_records[0]).toMatchObject({
      claim_type: 'FRANCHISE_DISCLOSURE_FACT',
      metric: 'kr-ediya-coffee',
      value_kind: 'EVIDENCED_FACT',
      unit: 'KRW',
      geographic_scope: { scope_type: 'NATIONAL', scope_id: 'KR', boundary_version: null },
      source: { authority: 'PRIMARY_DATA', source_type: 'DATASET' },
    })
  })

  it('does not fuzzy-match an unsupported internal brand id', async () => {
    const connector = createFranchiseDisclosureConnector({
      projectId,
      accessToken: async () => 'unused',
      fetch: vi.fn() as typeof fetch,
    })
    const result = await new McpToolRouter({ get_franchise_disclosure: connector }).call(
      'get_franchise_disclosure',
      { brand_id: 'unknown-brand', as_of: '2026-08-25' },
      scope,
    ) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'NOT_FOUND',
      data: [],
      missing_fields: ['franchise_disclosure'],
    })
  })
})
