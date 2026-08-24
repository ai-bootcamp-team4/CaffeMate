import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

export type FranchiseRagClaimType =
  | 'FRANCHISE_INDIVIDUAL_ELIGIBILITY'
  | 'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE'

interface FranchiseSourceDefinition {
  brandId: string
  displayName: string
  sourceRef: string
  sourceIdPrefix: string
  eligibilityQuery: string
  openingCostQuery: string
}

const SOURCE_FAMILY = 'COMPANY_OFFICIAL_FRANCHISE'
const GCS_BUCKET = 'proj-aj20-211200020328-caffemate-grounding'

const SOURCES: readonly FranchiseSourceDefinition[] = [
  {
    brandId: 'kr-compose-coffee',
    displayName: '컴포즈커피',
    sourceRef: 'https://composecoffee.com/composefranchise',
    sourceIdPrefix: 'compose-official',
    eligibilityQuery: '컴포즈커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내',
    openingCostQuery: '컴포즈커피 공식 창업 비용 10평 15평 포함 제외 항목',
  },
  {
    brandId: 'kr-mega-mgc-coffee',
    displayName: '메가MGC커피',
    sourceRef: 'https://www.mega-mgccoffee.com/',
    sourceIdPrefix: 'mega-mgc-official',
    eligibilityQuery: '메가MGC커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내',
    openingCostQuery: '메가MGC커피 공식 창업 비용 10평 포함 제외 항목',
  },
  {
    brandId: 'kr-ediya-coffee',
    displayName: '이디야커피',
    sourceRef: 'https://www.ediya.com/C/contents/franchise_02.html',
    sourceIdPrefix: 'ediya-official',
    eligibilityQuery: '이디야커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내',
    openingCostQuery: '이디야커피 공식 창업 비용 가맹비 월 로열티 포함 제외 항목',
  },
] as const

const CLAIMS: readonly {
  claimType: FranchiseRagClaimType
  slug: 'eligibility' | 'opening-cost'
  titleSuffix: string
}[] = [
  {
    claimType: 'FRANCHISE_INDIVIDUAL_ELIGIBILITY',
    slug: 'eligibility',
    titleSuffix: '공식 가맹 안내',
  },
  {
    claimType: 'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE',
    slug: 'opening-cost',
    titleSuffix: '공식 창업비 안내',
  },
] as const

export function buildFranchiseRagQueryPlan(): {
  brandId: string
  claimType: FranchiseRagClaimType
  query: string
}[] {
  return SOURCES.flatMap((source) => [
    {
      brandId: source.brandId,
      claimType: 'FRANCHISE_INDIVIDUAL_ELIGIBILITY' as const,
      query: source.eligibilityQuery,
    },
    {
      brandId: source.brandId,
      claimType: 'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE' as const,
      query: source.openingCostQuery,
    },
  ])
}

export function buildFranchiseRagImportPlan(repositoryRoot: string): {
  sourceId: string
  brandId: string
  sourceFamily: string
  claimType: FranchiseRagClaimType
  title: string
  sourceRef: string
  sourceDate: string
  localPath: string
  gcsUri: string
  contentDigest: string
}[] {
  return SOURCES.flatMap((source) => CLAIMS.map((claim) => {
    const localPath = resolve(
      repositoryRoot,
      'rag/data/franchise-official',
      source.brandId,
      `${claim.slug}.md`,
    )
    const content = readFileSync(localPath)
    return {
      sourceId: `${source.sourceIdPrefix}-${claim.slug}`,
      brandId: source.brandId,
      sourceFamily: SOURCE_FAMILY,
      claimType: claim.claimType,
      title: `${source.displayName} ${claim.titleSuffix}`,
      sourceRef: source.sourceRef,
      sourceDate: '2026-08-25',
      localPath,
      gcsUri: `gs://${GCS_BUCKET}/official/franchise/${source.brandId}/2026-08-25/${claim.slug}.md`,
      contentDigest: `sha256:${createHash('sha256').update(content).digest('hex')}`,
    }
  }))
}
