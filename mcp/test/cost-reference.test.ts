import { describe, expect, it } from 'vitest'
import { createCostReferenceConnector } from '../src/cost-reference'
import { McpToolRouter } from '../src/router'

const scope = {
  ventureProjectId: 'venture-1',
  workflowRunId: 'workflow-1',
  requestId: 'request-1',
}

describe('cost reference connector', () => {
  it('returns the minimum-wage schedule effective on the requested date', async () => {
    const connector = createCostReferenceConnector({
      now: () => new Date('2026-08-25T08:00:00Z'),
    })

    const result = await new McpToolRouter({ get_cost_reference: connector }).call(
      'get_cost_reference',
      {
        reference_types: ['MINIMUM_WAGE'],
        as_of: '2026-08-25',
      },
      scope,
    ) as Record<string, any>

    expect(result).toMatchObject({
      status: 'OK',
      tool_name: 'get_cost_reference',
      project_id: 'venture-1',
      data: [{
        reference_type: 'MINIMUM_WAGE',
        effective_from: '2026-01-01',
        effective_to: '2026-12-31',
        hourly_rate_krw: 10_320,
        monthly_equivalent_hours: 209,
        monthly_equivalent_krw: 2_156_880,
      }],
    })
    expect(result.evidence_records).toHaveLength(1)
    expect(result.evidence_records[0]).toMatchObject({
      claim_type: 'LABOR_COST_REFERENCE',
      metric: 'MINIMUM_WAGE_MONTHLY_209H',
      value: { kind: 'INTEGER', value: 2_156_880 },
      unit: 'KRW/month',
      value_kind: 'EVIDENCED_FACT',
      geographic_scope: {
        scope_type: 'NATIONAL',
        scope_id: 'KR',
        boundary_version: null,
      },
      source: {
        title: '최저임금위원회 연도별 최저임금',
        authority: 'PRIMARY_OFFICIAL',
        source_type: 'WEB',
        published_or_data_date: '2025-08-05',
      },
    })
  })

  it('selects the already-published 2027 schedule only after its effective date', async () => {
    const connector = createCostReferenceConnector()

    const result = await new McpToolRouter({ get_cost_reference: connector }).call(
      'get_cost_reference',
      {
        reference_types: ['MINIMUM_WAGE'],
        as_of: '2027-01-02',
      },
      scope,
    ) as Record<string, any>

    expect(result.data).toEqual([
      expect.objectContaining({
        effective_from: '2027-01-01',
        effective_to: '2027-12-31',
        hourly_rate_krw: 10_700,
        monthly_equivalent_krw: 2_236_300,
      }),
    ])
  })
})