import { RAG_REGION } from '../../rag/src/config'
import { RetrievalCoordinator } from '../../rag/src/retrieval'
import { createVertexRagBackend } from '../../rag/src/vertex-rag-backend'
import { createConnectorRegistry } from './connectors'
import { createBigQueryGroundingConnectors } from './bigquery-grounding'
import { createFranchiseCatalogConnector } from './franchise-catalog'
import { mapOfficialRagContext } from './official-rag'
import { createOfficialRagHealthSource } from './official-rag-health'
import { createRagMcpConnectors } from './rag-connectors'
import { MCP_PRODUCTION_TOOL_NAMES } from './manifest'
import type { McpConnectorRegistry } from './router'

export interface ProductionMcpConnectorOptions {
  projectId: string
  officialCorpusResource: string
  accessToken: () => Promise<string>
  jusoApiKey?: string
  fetch?: typeof globalThis.fetch
  now?: () => Date
  officialRagHealthTimeoutMs?: number
  groundingDatasetId?: string
}

function validateOfficialCorpusResource(projectId: string, resource: string): void {
  const prefix = `projects/${projectId}/locations/${RAG_REGION}/ragCorpora/`
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
    sourceHealthSources: [createOfficialRagHealthSource({
      officialCorpusResource: options.officialCorpusResource,
      accessToken: options.accessToken,
      fetch: fetchImpl,
      ...(options.officialRagHealthTimeoutMs !== undefined
        ? { timeoutMs: options.officialRagHealthTimeoutMs }
        : {}),
    })],
  })
  const officialBackend = createVertexRagBackend({
    projectId: options.projectId,
    region: RAG_REGION,
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
  const grounding = createBigQueryGroundingConnectors({
    projectId: options.projectId,
    datasetId: options.groundingDatasetId,
    location: RAG_REGION,
    accessToken: options.accessToken,
    fetch: fetchImpl,
    now,
  })
  const connectors: McpConnectorRegistry = {
    ...base,
    ...grounding,
    list_franchise_universe: createFranchiseCatalogConnector({ now }),
    retrieve_official_documents: rag.retrieve_official_documents,
  }
  const configured = Object.keys(connectors).sort()
  const expected = [...MCP_PRODUCTION_TOOL_NAMES].sort()
  if (JSON.stringify(configured) !== JSON.stringify(expected)) {
    throw new Error('MCP_PRODUCTION_CAPABILITY_MISMATCH')
  }
  return connectors
}
