import type {
  OfficialRetrievalInput,
  ProjectCorpusMapping,
  ProjectRetrievalInput,
  RagHit,
  RetrievalCoordinator,
} from '../../rag/src/retrieval'
import { getMcpToolDefinition } from './manifest'
import type { McpConnectorRegistry, McpScopeContext } from './router'

interface OfficialDocumentsInput {
  query: string
  source_families: OfficialRetrievalInput['sourceFamilies']
  as_of: string
  limit: number
}

interface ProjectDocumentsInput {
  query: string
  document_type?: string | null
  document_revision_ids: string[]
  limit: number
}

export interface RagMcpConnectorDependencies {
  retrieval: RetrievalCoordinator
  resolveProjectCorpusMapping: (scope: McpScopeContext) => Promise<ProjectCorpusMapping | null>
  now?: () => string
}

function toolVersion(name: 'retrieve_official_documents' | 'retrieve_project_documents'): string {
  const definition = getMcpToolDefinition(name)
  if (!definition) throw new Error(`MCP_RAG_TOOL_DEFINITION_MISSING: ${name}`)
  return definition.version
}

function documentHit(hit: RagHit) {
  return {
    document_revision_id: hit.documentRevisionId,
    title: hit.title,
    anchor: hit.anchor,
    excerpt: hit.excerpt,
    source_date: hit.sourceDate,
    evidence_id: hit.evidenceId,
  }
}

function resultEnvelope(
  toolName: 'retrieve_official_documents' | 'retrieve_project_documents',
  scope: McpScopeContext,
  hits: RagHit[],
  observedAt: string,
) {
  const found = hits.length > 0
  const sources = new Map<string, NonNullable<RagHit['source']>>()
  for (const hit of hits) {
    if (!hit.source) continue
    sources.set(`${hit.source.sourceId}\n${hit.source.sourceRef}\n${hit.source.contentDigest}`, hit.source)
  }
  return {
    schema_version: '1.0.0',
    request_id: scope.requestId,
    tool_name: toolName,
    tool_version: toolVersion(toolName),
    status: found ? 'OK' : 'NOT_FOUND',
    project_id: scope.ventureProjectId,
    evidence_records: [],
    missing_fields: found ? [] : ['document_hits'],
    conflicts: [],
    source_trace: [...sources.values()].map((source) => ({
      source_id: source.sourceId,
      source_ref: source.sourceRef,
      data_date: source.dataDate,
      retrieved_at: observedAt,
      content_digest: source.contentDigest,
    })),
    error_codes: [],
    observed_at: observedAt,
    data: hits.map(documentHit),
  }
}

export function createRagMcpConnectors(dependencies: RagMcpConnectorDependencies): McpConnectorRegistry {
  const now = dependencies.now ?? (() => new Date().toISOString())
  return {
    retrieve_official_documents: async (input, scope) => {
      const value = input as OfficialDocumentsInput
      const hits = await dependencies.retrieval.retrieveOfficial({
        query: value.query,
        sourceFamilies: [...value.source_families],
        asOf: value.as_of,
        limit: value.limit,
      })
      return resultEnvelope('retrieve_official_documents', scope, hits, now())
    },
    retrieve_project_documents: async (input, scope) => {
      const value = input as ProjectDocumentsInput
      const mapping = await dependencies.resolveProjectCorpusMapping(scope)
      const retrievalInput: ProjectRetrievalInput = {
        query: value.query,
        documentRevisionIds: [...value.document_revision_ids],
        limit: value.limit,
        documentType: value.document_type ?? null,
      }
      const hits = await dependencies.retrieval.retrieveProject(
        retrievalInput,
        { ventureProjectId: scope.ventureProjectId },
        mapping,
      )
      return resultEnvelope('retrieve_project_documents', scope, hits, now())
    },
  }
}
