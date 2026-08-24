import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import registry from '../../../mcp/data/franchise-rag-file-registry-20260825.json'
import {
  buildFranchiseRagImportPlan,
  buildFranchiseRagQueryPlan,
} from './franchise-rag-preparation'

describe('franchise RAG preparation', () => {
  it('prepares two claim-specific documents for each supported brand', () => {
    const plan = buildFranchiseRagImportPlan(resolve(process.cwd()))

    expect(plan).toHaveLength(6)
    expect(new Set(plan.map((item) => item.brandId))).toEqual(new Set([
      'kr-compose-coffee',
      'kr-mega-mgc-coffee',
      'kr-ediya-coffee',
    ]))
    expect(new Set(plan.map((item) => item.claimType))).toEqual(new Set([
      'FRANCHISE_INDIVIDUAL_ELIGIBILITY',
      'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE',
    ]))
    for (const item of plan) {
      const content = readFileSync(item.localPath, 'utf8')
      expect(content).toContain(`source_ref: ${item.sourceRef}`)
      expect(content).toContain(`source_family: ${item.sourceFamily}`)
      expect(content).toContain(`claim_type: ${item.claimType}`)
      expect(content).toContain(`brand_id: ${item.brandId}`)
      expect(item.contentDigest).toMatch(/^sha256:[0-9a-f]{64}$/)
      expect(item.gcsUri).toContain(`/official/franchise/${item.brandId}/`)
    }
  })

  it('builds deterministic brand-and-claim-specific retrieval queries', () => {
    expect(buildFranchiseRagQueryPlan()).toEqual([
      {
        brandId: 'kr-compose-coffee',
        claimType: 'FRANCHISE_INDIVIDUAL_ELIGIBILITY',
        query: '컴포즈커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내',
      },
      {
        brandId: 'kr-compose-coffee',
        claimType: 'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE',
        query: '컴포즈커피 공식 창업 비용 10평 15평 포함 제외 항목',
      },
      {
        brandId: 'kr-mega-mgc-coffee',
        claimType: 'FRANCHISE_INDIVIDUAL_ELIGIBILITY',
        query: '메가MGC커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내',
      },
      {
        brandId: 'kr-mega-mgc-coffee',
        claimType: 'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE',
        query: '메가MGC커피 공식 창업 비용 10평 포함 제외 항목',
      },
      {
        brandId: 'kr-ediya-coffee',
        claimType: 'FRANCHISE_INDIVIDUAL_ELIGIBILITY',
        query: '이디야커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내',
      },
      {
        brandId: 'kr-ediya-coffee',
        claimType: 'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE',
        query: '이디야커피 공식 창업 비용 가맹비 월 로열티 포함 제외 항목',
      },
    ])
  })

  it('keeps the runtime registry aligned with the prepared import plan', () => {
    const plan = buildFranchiseRagImportPlan(resolve(process.cwd()))

    expect(registry.sources.map((source) => ({
      sourceId: source.sourceId,
      brandId: source.brandId,
      sourceFamily: source.sourceFamily,
      claimType: source.claimType,
      sourceRef: source.sourceRef,
      sourceUri: source.sourceUri,
      contentDigest: source.contentDigest,
    }))).toEqual(plan.map((item) => ({
      sourceId: item.sourceId,
      brandId: item.brandId,
      sourceFamily: item.sourceFamily,
      claimType: item.claimType,
      sourceRef: item.sourceRef,
      sourceUri: item.gcsUri,
      contentDigest: item.contentDigest,
    })))
  })
})
