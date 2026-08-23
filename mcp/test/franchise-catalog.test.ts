import { describe, expect, it } from 'vitest'
import { createFranchiseCatalogConnector } from '../src/franchise-catalog'
import { McpToolRouter } from '../src/router'

const scope = {
  ventureProjectId: 'project-1',
  workflowRunId: 'workflow-1',
  requestId: 'request-1',
}

interface FranchiseResult {
  status: string
  data: Array<{
    display_name: string
    individual_franchise_eligibility: string
    disclosure_status: string
  }>
  evidence_records: Array<{
    project_id: string
    source: { authority: string }
    missing_context: string[]
  }>
  missing_fields: string[]
}

describe('official franchise catalog connector', () => {
  it('returns real brands with company-official eligibility evidence', async () => {
    const connector = createFranchiseCatalogConnector({
      now: () => new Date('2026-08-23T08:00:00Z'),
    })
    const result = await new McpToolRouter({ list_franchise_universe: connector }).call(
      'list_franchise_universe',
      { business_category: 'CAFE', as_of: '2026-08-23' },
      scope,
    ) as FranchiseResult

    expect(result.status).toBe('PARTIAL')
    expect(result.data.map((brand) => brand.display_name)).toEqual([
      '이디야커피',
      '메가MGC커피',
    ])
    expect(result.data.every((brand) => (
      brand.individual_franchise_eligibility === 'VERIFIED'
      && brand.disclosure_status === 'MISSING'
    ))).toBe(true)
    expect(result.evidence_records).toHaveLength(2)
    expect(result.evidence_records.every((record) => (
      record.project_id === 'project-1'
      && record.source.authority === 'COMPANY_OFFICIAL'
      && record.missing_context.includes('AREA_AVAILABILITY_REQUIRES_HEADQUARTERS_CONFIRMATION')
    ))).toBe(true)
  })

  it('does not expose a snapshot after the requested historical cutoff', async () => {
    const connector = createFranchiseCatalogConnector()
    const result = await new McpToolRouter({ list_franchise_universe: connector }).call(
      'list_franchise_universe',
      { business_category: 'CAFE', as_of: '2026-08-22' },
      scope,
    ) as FranchiseResult

    expect(result).toMatchObject({
      status: 'NOT_FOUND',
      data: [],
      evidence_records: [],
      missing_fields: ['franchise_universe_as_of'],
    })
  })
})
