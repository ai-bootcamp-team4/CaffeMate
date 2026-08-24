import { describe, expect, it } from 'vitest'
import {
  createFranchiseCatalogConnector,
  getFranchiseResearchSnapshot,
} from '../src/franchise-catalog'
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
    finance_profile: {
      coverage: string
      value_kind: string
      known_initial_cost_range_krw: { low: number; base: number; high: number } | null
      source_refs: string[]
    }
  }>
  evidence_records: Array<{
    evidence_id: string
    project_id: string
    source: { authority: string }
    missing_context: string[]
  }>
  missing_fields: string[]
}

describe('official franchise catalog connector', () => {
  it('keeps structured filtering facts separate from RAG source metadata', () => {
    const snapshot = getFranchiseResearchSnapshot()
    const starbucks = snapshot.catalog.brands.find((brand) => (
      brand.brand_id === 'kr-starbucks-korea'
    ))

    expect(starbucks).toMatchObject({
      individual_franchise_eligibility: 'INELIGIBLE',
      proposal_eligible: false,
      usage: 'COMPETITOR_REFERENCE',
    })
    expect(snapshot.ragSources.every((source) => (
      source.source_ref.startsWith('https://')
      && source.checked_at === '2026-08-24'
      && source.source_anchor.length > 0
    ))).toBe(true)
    expect(snapshot.ragSources.some((source) => (
      source.brand_id === 'kr-starbucks-korea'
      && source.source_family === 'GOVERNMENT_STATISTICS_GUIDE'
    ))).toBe(true)
  })

  it('returns only brands whose individual franchise recruitment is verified', async () => {
    const connector = createFranchiseCatalogConnector({
      now: () => new Date('2026-08-24T08:00:00Z'),
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
      '컴포즈커피',
      '빽다방',
      '더벤티',
      '커피베이',
      '할리스',
      '투썸플레이스',
      '매머드커피',
    ])
    expect(result.data.every((brand) => (
      brand.individual_franchise_eligibility === 'VERIFIED'
      && brand.disclosure_status === 'MISSING'
    ))).toBe(true)
    expect(result.data.some((brand) => brand.display_name === '스타벅스')).toBe(false)
    expect(result.data.some((brand) => (
      brand.individual_franchise_eligibility !== 'VERIFIED'
    ))).toBe(false)
    expect(result.data.find((brand) => brand.display_name === '이디야커피')?.finance_profile)
      .toMatchObject({
        coverage: 'PARTIAL',
        value_kind: 'EVIDENCED_FACT',
        known_initial_cost_range_krw: {
          low: 27000000,
          base: 27000000,
          high: 27000000,
        },
      })
    expect(result.data.find((brand) => brand.display_name === '컴포즈커피')?.finance_profile)
      .toMatchObject({
        coverage: 'UNKNOWN',
        value_kind: 'UNKNOWN',
        known_initial_cost_range_krw: null,
      })
    expect(result.evidence_records).toHaveLength(15)
    expect(result.evidence_records.filter((record) => (
      record.evidence_id.startsWith('franchise-eligibility:')
    )).every((record) => (
      record.project_id === 'project-1'
      && record.source.authority === 'COMPANY_OFFICIAL'
      && record.missing_context.includes('AREA_AVAILABILITY_REQUIRES_HEADQUARTERS_CONFIRMATION')
    ))).toBe(true)
    expect(result.evidence_records.some((record) => (
      record.evidence_id === 'franchise-cost:kr-mega-mgc-coffee:2026-08-24'
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
