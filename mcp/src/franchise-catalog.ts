import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { McpConnector } from './router'

interface FranchiseBrandSnapshot {
  brand_id: string
  display_name: string
  source_id: string
  source_ref: string
  source_title: string
  source_anchor: string
  eligibility_evidence_id: string
  individual_franchise_eligibility: 'VERIFIED'
  disclosure_status: 'MISSING'
}

interface FranchiseCatalogSnapshot {
  schema_version: '1.0.0'
  data_date: string
  brands: FranchiseBrandSnapshot[]
}

const SNAPSHOT_TEXT = readFileSync(
  resolve(process.cwd(), 'mcp/data/franchise-brands-20260823.json'),
  'utf8',
)
const SNAPSHOT = JSON.parse(SNAPSHOT_TEXT) as FranchiseCatalogSnapshot
const SNAPSHOT_DIGEST = digest(SNAPSHOT_TEXT)

function digest(value: string): string {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function eligibilityEvidence(
  brand: FranchiseBrandSnapshot,
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
      title: brand.source_title,
      source_ref: brand.source_ref,
      authority: 'COMPANY_OFFICIAL',
      source_type: 'WEB',
      published_or_data_date: SNAPSHOT.data_date,
      source_observed_at: observedAt,
      document_version: SNAPSHOT.data_date,
      checksum: SNAPSHOT_DIGEST,
    },
    original_anchor: {
      anchor_type: 'SECTION',
      locator: brand.source_anchor,
      excerpt_hash: digest(`${brand.brand_id}:${brand.source_anchor}`),
    },
    freshness_status: 'FRESH',
    conflict_status: 'NONE',
    retrieved_at: observedAt,
    missing_context: [
      'AREA_AVAILABILITY_REQUIRES_HEADQUARTERS_CONFIRMATION',
      'FRANCHISE_DISCLOSURE_NOT_CONNECTED',
    ],
    durable_evidence_refs: [brand.source_ref],
  }
}

export function createFranchiseCatalogConnector(options: { now?: () => Date } = {}): McpConnector {
  const clock = options.now ?? (() => new Date())
  return async (rawInput, scope) => {
    const input = rawInput as { business_category: 'CAFE'; as_of: string }
    const now = clock()
    const observedAt = now.toISOString()
    const available = input.business_category === 'CAFE' && input.as_of >= SNAPSHOT.data_date
    const brands = available ? SNAPSHOT.brands : []
    return {
      schema_version: '1.0.0',
      request_id: scope.requestId,
      tool_name: 'list_franchise_universe',
      tool_version: '1.0.0',
      status: brands.length ? 'PARTIAL' : 'NOT_FOUND',
      project_id: scope.ventureProjectId,
      evidence_records: brands.map((brand) => eligibilityEvidence(
        brand,
        scope.ventureProjectId,
        observedAt,
      )),
      missing_fields: brands.length
        ? ['franchise_disclosure', 'area_availability_hq_confirmation']
        : ['franchise_universe_as_of'],
      conflicts: [],
      source_trace: brands.map((brand) => ({
        source_id: brand.source_id,
        source_ref: brand.source_ref,
        data_date: SNAPSHOT.data_date,
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
      })),
    }
  }
}
