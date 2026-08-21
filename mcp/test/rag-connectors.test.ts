import { describe, expect, it, vi } from 'vitest'
import { RetrievalCoordinator, type ProjectCorpusMapping, type RagHit } from '../../rag/src/retrieval'
import { McpToolRouter } from '../src/router'
import { createRagMcpConnectors } from '../src/rag-connectors'

const scope = {
  ventureProjectId: 'project-1',
  workflowRunId: 'workflow-1',
  requestId: 'request-1',
}
const officialCorpus = 'projects/proj-aj20-211200020328/locations/asia-northeast3/ragCorpora/5148740273991319552'

function hit(documentRevisionId = 'docrev-1'): RagHit {
  return {
    documentRevisionId,
    title: '공식 문서',
    anchor: 'page:1',
    excerpt: '검색된 문서 내용',
    sourceDate: '2026-08-01',
    evidenceId: 'ev-1',
  }
}

function mapping(): ProjectCorpusMapping {
  return {
    ventureProjectId: 'project-1',
    corpusId: 'projects/proj-aj20-211200020328/locations/asia-northeast3/ragCorpora/9876',
    documentRevisionIds: ['docrev-1'],
    ragFileIdsByDocumentRevisionId: { 'docrev-1': 'rag-file-1' },
  }
}

describe('MCP RAG connectors', () => {
  it('returns schema-valid official document hits from the configured Vertex RAG coordinator', async () => {
    const official = vi.fn(async () => [hit('official-rev-1')])
    const retrieval = new RetrievalCoordinator({ official }, { officialCorpusId: officialCorpus })
    const router = new McpToolRouter(createRagMcpConnectors({
      retrieval,
      resolveProjectCorpusMapping: async () => null,
      now: () => '2026-08-21T12:00:00Z',
    }))

    const result = await router.call('retrieve_official_documents', {
      query: '가맹사업법',
      source_families: ['LAW'],
      as_of: '2026-08-21',
      limit: 5,
    }, scope) as Record<string, unknown>

    expect(official).toHaveBeenCalledWith(expect.objectContaining({
      corpusId: officialCorpus,
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
    }))
    expect(result).toMatchObject({
      schema_version: '1.0.0',
      request_id: 'request-1',
      tool_name: 'retrieve_official_documents',
      tool_version: '1.0.0',
      status: 'OK',
      project_id: 'project-1',
      observed_at: '2026-08-21T12:00:00Z',
      data: [{
        document_revision_id: 'official-rev-1',
        title: '공식 문서',
        anchor: 'page:1',
        excerpt: '검색된 문서 내용',
        source_date: '2026-08-01',
        evidence_id: 'ev-1',
      }],
    })
  })

  it('resolves the project corpus mapping from validated scope and preserves the revision fence', async () => {
    const project = vi.fn(async () => [hit()])
    const resolveProjectCorpusMapping = vi.fn(async () => mapping())
    const retrieval = new RetrievalCoordinator({ project })
    const router = new McpToolRouter(createRagMcpConnectors({
      retrieval,
      resolveProjectCorpusMapping,
      now: () => '2026-08-21T12:00:00Z',
    }))

    const result = await router.call('retrieve_project_documents', {
      query: '임대료',
      document_type: 'LEASE',
      document_revision_ids: ['docrev-1'],
      limit: 5,
    }, scope) as Record<string, unknown>

    expect(resolveProjectCorpusMapping).toHaveBeenCalledWith(scope)
    expect(project).toHaveBeenCalledWith(expect.objectContaining({
      ventureProjectId: 'project-1',
      documentRevisionIds: ['docrev-1'],
      ragFileIds: ['rag-file-1'],
      documentType: 'LEASE',
    }))
    expect(result).toMatchObject({
      request_id: 'request-1',
      tool_name: 'retrieve_project_documents',
      status: 'OK',
      project_id: 'project-1',
    })
  })

  it('returns NOT_FOUND instead of treating an empty retrieval as successful evidence', async () => {
    const retrieval = new RetrievalCoordinator({ official: async () => [] }, { officialCorpusId: officialCorpus })
    const router = new McpToolRouter(createRagMcpConnectors({
      retrieval,
      resolveProjectCorpusMapping: async () => null,
      now: () => '2026-08-21T12:00:00Z',
    }))

    const result = await router.call('retrieve_official_documents', {
      query: '없는 문서',
      source_families: ['LAW'],
      as_of: '2026-08-21',
      limit: 5,
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'NOT_FOUND',
      data: [],
      missing_fields: ['document_hits'],
    })
  })
})
