import { describe, expect, it, vi } from 'vitest'
import { createPropertyReferenceConnector } from '../src/property-reference'
import { McpToolRouter } from '../src/router'

const projectId = 'proj-aj20-211200020328'
const scope = { ventureProjectId: 'venture-1', workflowRunId: 'workflow-1', requestId: 'request-1' }

describe('property reference connector', () => {
  it('returns latest approved parent-region REB rent references', async () => {
    const fetcher = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body))
      expect(body.query).toContain('commercial_rent_manifest')
      expect(body.query).toContain('commercial_rent_reference')
      expect(body.query).toContain('EXTRACT(YEAR FROM @as_of)')
      expect(body.queryParameters).toEqual([
        {
          name: 'administrative_code',
          parameterType: { type: 'STRING' },
          parameterValue: { value: '1144012300' },
        },
        {
          name: 'as_of',
          parameterType: { type: 'DATE' },
          parameterValue: { value: '2026-08-25' },
        },
      ])
      return Response.json({
        jobComplete: true,
        rows: [
          { f: [{ v: JSON.stringify({
            ingestion_id: 'rent-ingestion-1',
            period_code: '2026Q2',
            region_code: '11',
            region_name: '서울',
            property_class: 'SMALL_RETAIL',
            effective_rent_krw_per_sqm_month: 42500,
            conversion_rate_bps: 710,
            coverage_status: 'PARENT_REGION',
            floor_basis: 'FIRST_FLOOR',
            rent_table_id: 'T248223134698125',
            conversion_table_id: 'T246253134905233',
            source_digests_json: JSON.stringify({
              T248223134698125: 'a'.repeat(64),
              T246253134905233: 'b'.repeat(64),
            }),
          }) }] },
          { f: [{ v: JSON.stringify({
            ingestion_id: 'rent-ingestion-1',
            period_code: '2026Q2',
            region_code: '11',
            region_name: '서울',
            property_class: 'MEDIUM_LARGE_RETAIL',
            effective_rent_krw_per_sqm_month: 56700,
            conversion_rate_bps: 710,
            coverage_status: 'PARENT_REGION',
            floor_basis: 'FIRST_FLOOR',
            rent_table_id: 'T244363134858603',
            conversion_table_id: 'T241883134877452',
            source_digests_json: JSON.stringify({
              T244363134858603: 'c'.repeat(64),
              T241883134877452: 'd'.repeat(64),
            }),
          }) }] },
        ],
      })
    }) as typeof fetch
    const connector = createPropertyReferenceConnector({
      projectId,
      accessToken: async () => 'access-token',
      fetch: fetcher,
      now: () => new Date('2026-08-25T04:00:00Z'),
    })

    const result = await new McpToolRouter({ get_property_reference: connector }).call(
      'get_property_reference',
      {
        administrative_code: '1144012300',
        boundary_version: 'MOIS_LEGAL_DONG_20260301',
        as_of: '2026-08-25',
      },
      scope,
    ) as Record<string, any>

    expect(result).toMatchObject({
      status: 'OK',
      tool_name: 'get_property_reference',
      project_id: 'venture-1',
      data: [
        {
          property_class: 'SMALL_RETAIL',
          effective_rent_krw_per_sqm_month: 42500,
          conversion_rate_bps: 710,
          period: '2026Q2',
          region_code: '11',
          region_name: '서울',
          coverage_status: 'PARENT_REGION',
          floor_basis: 'FIRST_FLOOR',
        },
        {
          property_class: 'MEDIUM_LARGE_RETAIL',
          effective_rent_krw_per_sqm_month: 56700,
          conversion_rate_bps: 710,
        },
      ],
    })
    expect(result.evidence_records).toHaveLength(2)
    expect(result.evidence_records[0]).toMatchObject({
      claim_type: 'PROPERTY_RENT_REFERENCE',
      metric: 'EFFECTIVE_RENT_AND_CONVERSION_RATE',
      value_kind: 'EVIDENCED_FACT',
      geographic_scope: { scope_type: 'REGION', scope_id: '11', boundary_version: null },
      source: {
        title: '한국부동산원 상업용부동산 임대동향조사',
        authority: 'PRIMARY_DATA',
        source_type: 'DATASET',
        published_or_data_date: '2026-06-30',
      },
    })
  })

  it('does not silently fall back when the selected region has no approved reference', async () => {
    const connector = createPropertyReferenceConnector({
      projectId,
      accessToken: async () => 'access-token',
      fetch: vi.fn(async () => Response.json({ jobComplete: true })) as typeof fetch,
    })
    const result = await new McpToolRouter({ get_property_reference: connector }).call(
      'get_property_reference',
      {
        administrative_code: '1144012300',
        boundary_version: 'MOIS_LEGAL_DONG_20260301',
        as_of: '2026-08-25',
      },
      scope,
    ) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'NOT_FOUND',
      data: [],
      missing_fields: ['regional_property_reference'],
    })
  })
})