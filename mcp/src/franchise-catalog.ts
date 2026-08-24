import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { McpConnector } from './router'

// 사용자 의도: 공식 근거로 개인 가맹이 확인된 브랜드만 Proposal Agent에 제공하고,
// 직영 브랜드와 근거가 부족한 브랜드는 비교·조사 데이터로만 보존한다.
export interface FranchiseFinanceProfile {
  currency: 'KRW'
  coverage: 'PARTIAL' | 'UNKNOWN' | 'NOT_APPLICABLE'
  value_kind: 'EVIDENCED_FACT' | 'DECLARED_ASSUMPTION' | 'UNKNOWN'
  known_initial_cost_range_krw: {
    low: number
    base: number
    high: number
  } | null
  reference_area_sqm: number | null
  monthly_royalty_krw: number | null
  evidence_refs: string[]
  source_refs: string[]
  scope_note: string
  missing_costs: string[]
}

export interface FranchiseBrandSnapshot {
  brand_id: string
  display_name: string
  individual_franchise_eligibility: 'VERIFIED' | 'INELIGIBLE' | 'UNVERIFIED'
  proposal_eligible: boolean
  usage: 'PROPOSAL_CANDIDATE' | 'COMPETITOR_REFERENCE' | 'RESEARCH_ONLY'
  eligibility_evidence_id: string
  eligibility_source_id: string
  disclosure_status: 'AVAILABLE' | 'MISSING' | 'STALE'
  finance_profile: FranchiseFinanceProfile
  missing_fields: string[]
}

export interface FranchiseCatalogSnapshot {
  schema_version: '2.0.0'
  snapshot_id: string
  data_date: string
  checked_at: string
  brands: FranchiseBrandSnapshot[]
}

export interface FranchiseRagSource {
  source_id: string
  brand_id: string
  source_family: 'COMPANY_OFFICIAL_FRANCHISE' | 'GOVERNMENT_STATISTICS_GUIDE'
  source_ref: string
  source_title: string
  source_anchor: string
  published_or_data_date: string | null
  checked_at: string
  content_type: 'text/html' | 'application/pdf'
  ingestion_status: 'READY'
  retrieval_usage: string[]
}

interface FranchiseRagSourceSnapshot {
  schema_version: '1.0.0'
  snapshot_id: string
  checked_at: string
  sources: FranchiseRagSource[]
}

const CATALOG_TEXT = readFileSync(
  resolve(process.cwd(), 'mcp/data/franchise-brands-20260823.json'),
  'utf8',
)
const RAG_SOURCES_TEXT = readFileSync(
  resolve(process.cwd(), 'mcp/data/franchise-rag-sources-20260824.json'),
  'utf8',
)
const CATALOG = JSON.parse(CATALOG_TEXT) as FranchiseCatalogSnapshot
const RAG_SOURCE_SNAPSHOT = JSON.parse(RAG_SOURCES_TEXT) as FranchiseRagSourceSnapshot
const SOURCE_BY_ID = validateResearchSnapshot(CATALOG, RAG_SOURCE_SNAPSHOT)
const SNAPSHOT_DIGEST = digest(`${CATALOG_TEXT}\n${RAG_SOURCES_TEXT}`)

function digest(value: string): string {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function validateResearchSnapshot(
  catalog: FranchiseCatalogSnapshot,
  ragSnapshot: FranchiseRagSourceSnapshot,
): ReadonlyMap<string, FranchiseRagSource> {
  if (catalog.schema_version !== '2.0.0' || ragSnapshot.schema_version !== '1.0.0') {
    throw new Error('지원하지 않는 프랜차이즈 데이터 스키마입니다.')
  }
  if (catalog.checked_at !== ragSnapshot.checked_at) {
    throw new Error('카탈로그와 RAG 근거 목록의 확인일이 다릅니다.')
  }

  const brandIds = new Set<string>()
  for (const brand of catalog.brands) {
    if (brandIds.has(brand.brand_id)) {
      throw new Error(`중복 프랜차이즈 브랜드 ID: ${brand.brand_id}`)
    }
    brandIds.add(brand.brand_id)
    const shouldBeEligible = brand.individual_franchise_eligibility === 'VERIFIED'
      && brand.usage === 'PROPOSAL_CANDIDATE'
    if (brand.proposal_eligible !== shouldBeEligible) {
      throw new Error(`Proposal 대상 상태가 일치하지 않습니다: ${brand.brand_id}`)
    }
    validateFinanceProfile(brand)
  }

  const sources = new Map<string, FranchiseRagSource>()
  for (const source of ragSnapshot.sources) {
    if (sources.has(source.source_id)) {
      throw new Error(`중복 프랜차이즈 근거 ID: ${source.source_id}`)
    }
    if (!brandIds.has(source.brand_id)) {
      throw new Error(`카탈로그에 없는 브랜드의 근거입니다: ${source.brand_id}`)
    }
    if (!source.source_ref.startsWith('https://') || !source.source_anchor) {
      throw new Error(`공식 근거 URL 또는 원문 위치가 없습니다: ${source.source_id}`)
    }
    sources.set(source.source_id, source)
  }

  for (const brand of catalog.brands) {
    const source = sources.get(brand.eligibility_source_id)
    if (!source || source.brand_id !== brand.brand_id) {
      throw new Error(`브랜드의 개인 가맹 근거를 찾을 수 없습니다: ${brand.brand_id}`)
    }
  }
  return sources
}

function validateFinanceProfile(brand: FranchiseBrandSnapshot): void {
  const profile = brand.finance_profile
  const range = profile.known_initial_cost_range_krw
  if (profile.value_kind === 'EVIDENCED_FACT') {
    if (!range || profile.evidence_refs.length === 0 || profile.source_refs.length === 0) {
      throw new Error(`공식 비용값에 근거가 없습니다: ${brand.brand_id}`)
    }
  } else if (range !== null) {
    throw new Error(`근거 없는 비용값이 입력되었습니다: ${brand.brand_id}`)
  }
  if (range && (range.low > range.base || range.base > range.high || range.low < 0)) {
    throw new Error(`비용 범위가 올바르지 않습니다: ${brand.brand_id}`)
  }
}

export function getFranchiseResearchSnapshot(): {
  catalog: FranchiseCatalogSnapshot
  ragSources: FranchiseRagSource[]
} {
  return structuredClone({
    catalog: CATALOG,
    ragSources: RAG_SOURCE_SNAPSHOT.sources,
  })
}

function eligibilityEvidence(
  brand: FranchiseBrandSnapshot,
  source: FranchiseRagSource,
  projectId: string,
  observedAt: string,
) {
  return {
    schema_version: '2.0.0',
    evidence_id: brand.eligibility_evidence_id,
    project_id: projectId,
    claim_type: 'FRANCHISE_UNIVERSE_ELIGIBILITY',
    metric: 'INDIVIDUAL_FRANCHISE_RECRUITMENT',
    value: { kind: 'STRING', value: '공식 가맹 안내 및 창업상담 접수 확인' },
    value_kind: 'EVIDENCED_FACT',
    unit: null,
    geographic_scope: {
      scope_type: 'NATIONAL',
      scope_id: 'KR',
      boundary_version: null,
    },
    source: {
      title: source.source_title,
      source_ref: source.source_ref,
      authority: 'COMPANY_OFFICIAL',
      source_type: source.content_type === 'application/pdf' ? 'PDF' : 'WEB',
      published_or_data_date: source.published_or_data_date,
      source_observed_at: observedAt,
      document_version: source.published_or_data_date,
      checksum: SNAPSHOT_DIGEST,
    },
    original_anchor: {
      anchor_type: 'SECTION',
      locator: source.source_anchor,
      excerpt_hash: digest(`${brand.brand_id}:${source.source_anchor}`),
    },
    freshness_status: 'FRESH',
    conflict_status: 'NONE',
    retrieved_at: observedAt,
    missing_context: [
      'AREA_AVAILABILITY_REQUIRES_HEADQUARTERS_CONFIRMATION',
      'FRANCHISE_DISCLOSURE_NOT_CONNECTED',
    ],
    durable_evidence_refs: [source.source_ref],
  }
}

function financeEvidence(
  brand: FranchiseBrandSnapshot,
  projectId: string,
  observedAt: string,
) {
  const profile = brand.finance_profile
  const range = profile.known_initial_cost_range_krw
  const evidenceId = profile.evidence_refs[0]
  const sourceRef = profile.source_refs[0]
  if (profile.value_kind !== 'EVIDENCED_FACT' || !range || !evidenceId || !sourceRef) {
    return null
  }
  return {
    schema_version: '2.0.0',
    evidence_id: evidenceId,
    project_id: projectId,
    claim_type: 'FRANCHISE_KNOWN_INITIAL_COST',
    metric: 'KNOWN_INITIAL_COST_RANGE',
    value: {
      kind: 'MONEY_RANGE',
      currency: 'KRW',
      low: range.low,
      base: range.base,
      high: range.high,
    },
    value_kind: 'EVIDENCED_FACT',
    unit: 'KRW',
    geographic_scope: {
      scope_type: 'NATIONAL',
      scope_id: 'KR',
      boundary_version: null,
    },
    source: {
      title: `${brand.display_name} 공식 창업비용 안내`,
      source_ref: sourceRef,
      authority: 'COMPANY_OFFICIAL',
      source_type: sourceRef.toLowerCase().endsWith('.pdf') ? 'PDF' : 'WEB',
      published_or_data_date: CATALOG.data_date,
      source_observed_at: observedAt,
      document_version: CATALOG.data_date,
      checksum: SNAPSHOT_DIGEST,
    },
    original_anchor: {
      anchor_type: 'SECTION',
      locator: profile.scope_note,
      excerpt_hash: digest(`${brand.brand_id}:${profile.scope_note}`),
    },
    freshness_status: 'FRESH',
    conflict_status: 'NONE',
    retrieved_at: observedAt,
    missing_context: profile.missing_costs,
    durable_evidence_refs: profile.source_refs,
  }
}

export function createFranchiseCatalogConnector(options: { now?: () => Date } = {}): McpConnector {
  const clock = options.now ?? (() => new Date())
  return async (rawInput, scope) => {
    const input = rawInput as { business_category: 'CAFE'; as_of: string }
    const observedAt = clock().toISOString()
    const available = input.business_category === 'CAFE' && input.as_of >= CATALOG.data_date
    const brands = available
      ? CATALOG.brands.filter((brand) => (
        brand.proposal_eligible
        && brand.individual_franchise_eligibility === 'VERIFIED'
      ))
      : []
    const sources = brands.map((brand) => {
      const source = SOURCE_BY_ID.get(brand.eligibility_source_id)
      if (!source) {
        throw new Error(`검증된 브랜드의 공식 근거가 없습니다: ${brand.brand_id}`)
      }
      return source
    })

    return {
      schema_version: '1.0.0',
      request_id: scope.requestId,
      tool_name: 'list_franchise_universe',
      tool_version: '1.0.0',
      status: brands.length ? 'PARTIAL' : 'NOT_FOUND',
      project_id: scope.ventureProjectId,
      evidence_records: brands.flatMap((brand, index) => {
        const records = [eligibilityEvidence(
          brand,
          sources[index],
          scope.ventureProjectId,
          observedAt,
        )]
        const finance = financeEvidence(brand, scope.ventureProjectId, observedAt)
        return finance ? [...records, finance] : records
      }),
      missing_fields: brands.length
        ? ['franchise_disclosure', 'area_availability_hq_confirmation']
        : ['franchise_universe_as_of'],
      conflicts: [],
      source_trace: sources.map((source) => ({
        source_id: source.source_id,
        source_ref: source.source_ref,
        data_date: source.published_or_data_date,
        retrieved_at: observedAt,
        content_digest: SNAPSHOT_DIGEST,
      })),
      error_codes: [],
      observed_at: observedAt,
      data: brands.map((brand) => ({
        brand_id: brand.brand_id,
        display_name: brand.display_name,
        individual_franchise_eligibility: brand.individual_franchise_eligibility,
        eligibility_evidence_id: brand.eligibility_evidence_id,
        disclosure_status: brand.disclosure_status,
        finance_profile: brand.finance_profile,
      })),
    }
  }
}
