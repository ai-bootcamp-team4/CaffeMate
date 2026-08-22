import { GCP_LOCATIONS } from '../../agents/src/registry'
import { RetrievalCoordinator } from '../../rag/src/retrieval'
import { createVertexRagBackend } from '../../rag/src/vertex-rag-backend'
import { createConnectorRegistry } from './connectors'
import { mapOfficialRagContext } from './official-rag'
import { createRagMcpConnectors } from './rag-connectors'
import type { McpConnectorRegistry } from './router'

export interface ProductionMcpConnectorOptions {
  projectId: string
  officialCorpusResource: string
  accessToken: () => Promise<string>
  jusoApiKey?: string
  fetch?: typeof globalThis.fetch
  now?: () => Date
}

function validateOfficialCorpusResource(projectId: string, resource: string): void {
  const prefix = `projects/${projectId}/locations/${GCP_LOCATIONS.rag}/ragCorpora/`
  const corpusId = resource.startsWith(prefix) ? resource.slice(prefix.length) : ''
  if (!projectId || !corpusId || corpusId.includes('/')) {
    throw new Error('MCP_RAG_CORPUS_CONFIGURATION_INVALID')
  }
}

export function createProductionMcpConnectors(options: ProductionMcpConnectorOptions): McpConnectorRegistry {
  validateOfficialCorpusResource(options.projectId, options.officialCorpusResource)
  const fetchImpl = options.fetch ?? globalThis.fetch
  const now = options.now ?? (() => new Date())
  const base = createConnectorRegistry({
    jusoApiKey: options.jusoApiKey,
    fetch: fetchImpl,
    now,
  })
  const officialBackend = createVertexRagBackend({
    projectId: options.projectId,
    region: GCP_LOCATIONS.rag,
    accessToken: options.accessToken,
    mapContext: mapOfficialRagContext,
    fetchImpl,
  })
  const retrieval = new RetrievalCoordinator(
    { official: officialBackend },
    { officialCorpusId: options.officialCorpusResource },
  )
  const rag = createRagMcpConnectors({
    retrieval,
    resolveProjectCorpusMapping: async () => null,
    now: () => now().toISOString(),
  })
  return {
    ...base,
    retrieve_official_documents: rag.retrieve_official_documents,
  }
}