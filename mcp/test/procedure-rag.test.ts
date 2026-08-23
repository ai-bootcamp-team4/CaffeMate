import { describe, expect, it, vi } from 'vitest'
import { RetrievalCoordinator, type RagHit } from '../../rag/src/retrieval'
import { createOfficialProcedureRagConnector } from '../src/procedure-rag'
import { McpToolRouter } from '../src/router'

const corpus = 'projects/proj/locations/asia-northeast3/ragCorpora/1'
const scope = {
  ventureProjectId: 'project-1',
  workflowRunId: 'workflow-1',
  requestId: 'request-1',
}

function hit(excerpt: string): RagHit {
  return {
    documentRevisionId: 'official-guide@2026-07-15',
    title: '커피전문점 영업 안내',
    anchor: 'https://official.example/guide#section-2',
    excerpt,
    sourceDate: '2026-07-15',
    evidenceId: 'rag:file-1:chunk-1',
    source: {
      sourceId: 'official-guide',
      sourceRef: 'https://official.example/guide',
      dataDate: '2026-07-15',
      contentDigest: 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    },
  }
}

describe('official procedure RAG connector', () => {
  it('turns only a matching Vertex RAG hit into a source-linked procedure and EvidenceRecord', async () => {
    const backend = vi.fn(async () => [
      hit('커피전문점은 휴게음식점 영업신고를 해야 합니다.'),
      {
        ...hit('사업자등록은 관할 세무서에서 신청합니다.'),
        title: '커피전문점 영업신고 및 사업자등록',
        evidenceId: 'rag:file-1:chunk-2',
      },
    ])
    const retrieval = new RetrievalCoordinator({ official: backend }, { officialCorpusId: corpus })
    const connector = createOfficialProcedureRagConnector({
      retrieval,
      now: () => '2026-08-23T12:30:00Z',
    })

    const result = await new McpToolRouter({ get_official_procedure: connector }).call(
      'get_official_procedure',
      {
        jurisdiction_code: '1168010300',
        procedure_type: 'FOOD_SERVICE_REPORT',
        as_of: '2026-08-23',
      },
      scope,
    ) as Record<string, unknown>

    expect(backend).toHaveBeenCalledWith(expect.objectContaining({
      corpusKind: 'OFFICIAL',
      query: '커피전문점 휴게음식점 영업신고 절차 필요 서류 관할 행정청',
      sourceFamilies: ['GOVERNMENT_GUIDE'],
      asOf: '2026-08-23',
      limit: 5,
    }))
    expect(result).toMatchObject({
      status: 'OK',
      data: [{
        step_order: 1,
        title: '커피전문점은 휴게음식점 영업신고를 해야 합니다.',
        required: true,
        evidence_id: 'procedure:FOOD_SERVICE_REPORT:rag:file-1:chunk-1',
      }],
      evidence_records: [{
        evidence_id: 'procedure:FOOD_SERVICE_REPORT:rag:file-1:chunk-1',
        project_id: 'project-1',
        claim_type: 'CAFE_OPENING_PROCEDURE_FOOD_SERVICE_REPORT',
        value: { kind: 'STRING', value: '커피전문점은 휴게음식점 영업신고를 해야 합니다.' },
        source: {
          source_ref: 'https://official.example/guide',
          authority: 'PRIMARY_OFFICIAL',
        },
      }],
      source_trace: [{
        source_id: 'official-guide',
        source_ref: 'https://official.example/guide',
      }],
    })
    expect(result.data as unknown[]).toHaveLength(1)
    expect(result.evidence_records as unknown[]).toHaveLength(1)
  })

  it('returns NOT_FOUND instead of inventing a procedure from an unrelated semantic hit', async () => {
    const retrieval = new RetrievalCoordinator(
      { official: async () => [hit('커피 원두와 장비를 준비할 수 있습니다.')] },
      { officialCorpusId: corpus },
    )
    const connector = createOfficialProcedureRagConnector({ retrieval })

    const result = await new McpToolRouter({ get_official_procedure: connector }).call(
      'get_official_procedure',
      {
        jurisdiction_code: '1168010300',
        procedure_type: 'FIRE_SAFETY',
        as_of: '2026-08-23',
      },
      scope,
    )

    expect(result).toMatchObject({
      status: 'NOT_FOUND',
      data: [],
      evidence_records: [],
      missing_fields: ['procedure_steps'],
      source_trace: [],
    })
  })
})
