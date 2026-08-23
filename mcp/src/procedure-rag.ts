import { createHash } from 'node:crypto'
import type { OfficialRetrievalInput, RagHit, RetrievalCoordinator } from '../../rag/src/retrieval'
import { getMcpToolDefinition } from './manifest'
import type { McpConnector, McpScopeContext } from './router'

type ProcedureType =
  | 'BUSINESS_REGISTRATION'
  | 'FOOD_SERVICE_REPORT'
  | 'FACILITY_REQUIREMENTS'
  | 'HYGIENE_EDUCATION'
  | 'SIGNAGE'
  | 'FIRE_SAFETY'

interface ProcedureInput {
  jurisdiction_code: string
  procedure_type: ProcedureType
  as_of: string
}

interface ProcedureQuery {
  query: string
  keywords: readonly string[]
}

const PROCEDURE_QUERIES: Readonly<Record<ProcedureType, ProcedureQuery>> = Object.freeze({
  BUSINESS_REGISTRATION: {
    query: '커피전문점 사업자등록 신청 절차 필요 서류 관할 세무서',
    keywords: ['사업자등록', '세무서'],
  },
  FOOD_SERVICE_REPORT: {
    query: '커피전문점 휴게음식점 영업신고 절차 필요 서류 관할 행정청',
    keywords: ['영업신고', '휴게음식점'],
  },
  FACILITY_REQUIREMENTS: {
    query: '커피전문점 휴게음식점 영업 시설기준 조리장 급수 환기',
    keywords: ['시설기준', '조리장', '급수', '환기'],
  },
  HYGIENE_EDUCATION: {
    query: '커피전문점 휴게음식점 영업자 위생교육 신규 교육',
    keywords: ['위생교육'],
  },
  SIGNAGE: {
    query: '커피전문점 옥외광고물 간판 신고 허가 절차',
    keywords: ['간판', '옥외광고물'],
  },
  FIRE_SAFETY: {
    query: '커피전문점 소방 안전시설 완비증명 점검 절차',
    keywords: ['소방', '안전시설', '완비증명'],
  },
})

export interface ProcedureRagDependencies {
  retrieval: RetrievalCoordinator
  now?: () => string
}

function digest(value: string): string {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function isGroundedForProcedure(hit: RagHit, query: ProcedureQuery): boolean {
  // The document title covers several procedures, so only the retrieved chunk
  // may prove that this particular procedure exists.
  const text = hit.excerpt.normalize('NFKC')
  return query.keywords.some((keyword) => text.includes(keyword))
}

function isRequired(excerpt: string): boolean {
  return /(하여야|해야|의무|필수|신고|등록|교육을\s*받)/u.test(excerpt)
}

function evidenceRecord(
  hit: RagHit,
  input: ProcedureInput,
  scope: McpScopeContext,
  observedAt: string,
) {
  if (!hit.source || !hit.sourceDate) return null
  const evidenceId = `procedure:${input.procedure_type}:${hit.evidenceId}`
  return {
    schema_version: '2.0.0',
    evidence_id: evidenceId,
    project_id: scope.ventureProjectId,
    claim_type: `CAFE_OPENING_PROCEDURE_${input.procedure_type}`,
    value: { kind: 'STRING', value: hit.excerpt },
    value_kind: 'EVIDENCED_FACT',
    unit: null,
    geographic_scope: {
      scope_type: 'REGION',
      scope_id: input.jurisdiction_code,
      boundary_version: null,
    },
    source: {
      title: hit.title,
      source_ref: hit.source.sourceRef,
      authority: 'PRIMARY_OFFICIAL',
      source_type: 'WEB',
      published_or_data_date: hit.sourceDate,
      source_observed_at: observedAt,
      document_version: hit.documentRevisionId,
      checksum: hit.source.contentDigest,
    },
    original_anchor: {
      anchor_type: 'SECTION',
      locator: hit.anchor,
      excerpt_hash: digest(hit.excerpt),
    },
    freshness_status: 'FRESH',
    conflict_status: 'NONE',
    retrieved_at: observedAt,
    missing_context: [],
    durable_evidence_refs: [hit.evidenceId, hit.documentRevisionId],
  }
}

export function createOfficialProcedureRagConnector(
  dependencies: ProcedureRagDependencies,
): McpConnector {
  const now = dependencies.now ?? (() => new Date().toISOString())
  const definition = getMcpToolDefinition('get_official_procedure')
  if (!definition) throw new Error('MCP_PROCEDURE_TOOL_DEFINITION_MISSING')

  return async (rawInput, scope, execution) => {
    const input = rawInput as ProcedureInput
    const query = PROCEDURE_QUERIES[input.procedure_type]
    const retrievalInput: OfficialRetrievalInput = {
      query: query.query,
      sourceFamilies: ['GOVERNMENT_GUIDE'],
      asOf: input.as_of,
      limit: 5,
      ...(execution?.signal ? { signal: execution.signal } : {}),
    }
    const hits = await dependencies.retrieval.retrieveOfficial(retrievalInput)
    const observedAt = now()
    const grounded = hits
      .filter((hit) => isGroundedForProcedure(hit, query))
      .map((hit) => ({ hit, evidence: evidenceRecord(hit, input, scope, observedAt) }))
      .filter((value): value is { hit: RagHit; evidence: NonNullable<ReturnType<typeof evidenceRecord>> } => (
        value.evidence !== null
      ))

    const sourceTrace = new Map<string, NonNullable<RagHit['source']>>()
    for (const { hit } of grounded) {
      if (hit.source) sourceTrace.set(hit.source.contentDigest, hit.source)
    }
    return {
      schema_version: '1.0.0',
      request_id: scope.requestId,
      tool_name: 'get_official_procedure',
      tool_version: definition.version,
      status: grounded.length ? 'OK' : 'NOT_FOUND',
      project_id: scope.ventureProjectId,
      evidence_records: grounded.map(({ evidence }) => evidence),
      missing_fields: grounded.length ? [] : ['procedure_steps'],
      conflicts: [],
      source_trace: [...sourceTrace.values()].map((source) => ({
        source_id: source.sourceId,
        source_ref: source.sourceRef,
        data_date: source.dataDate,
        retrieved_at: observedAt,
        content_digest: source.contentDigest,
      })),
      error_codes: [],
      observed_at: observedAt,
      data: grounded.map(({ hit, evidence }, index) => ({
        step_order: index + 1,
        title: hit.excerpt,
        required: isRequired(hit.excerpt),
        authority: hit.title,
        source_date: hit.sourceDate,
        evidence_id: evidence.evidence_id,
      })),
    }
  }
}
