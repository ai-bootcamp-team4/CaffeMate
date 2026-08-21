import { GCP_LOCATIONS } from '../../agents/src/registry'
import type { RagBackend, RagBackendRequest, RagHit } from './retrieval'

export class VertexRagError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
  ) {
    super(`${code}: ${message}`)
    this.name = 'VertexRagError'
  }
}

export interface VertexRagContext {
  sourceUri: string
  sourceDisplayName: string
  text: string
  chunk?: unknown
  score?: number
}

export interface VertexRagBackendOptions {
  projectId: string
  region: typeof GCP_LOCATIONS.rag
  accessToken: () => Promise<string>
  mapContext: (context: VertexRagContext, request: RagBackendRequest) => RagHit | null
  fetchImpl?: typeof fetch
}

interface RawVertexRagContext {
  sourceUri?: string
  sourceDisplayName?: string
  text?: string
  chunk?: unknown
  score?: number
}

interface VertexRetrieveContextsResponse {
  contexts?: {
    contexts?: RawVertexRagContext[]
  }
}

function corpusResource(projectId: string, region: string, corpusId: string): string {
  const expectedPrefix = `projects/${projectId}/locations/${region}/ragCorpora/`
  if (corpusId.startsWith('projects/')) {
    if (!corpusId.startsWith(expectedPrefix) || corpusId.slice(expectedPrefix.length).includes('/')) {
      throw new VertexRagError('RAG_CORPUS_SCOPE_MISMATCH', 'RAG corpus belongs to another project or region')
    }
    return corpusId
  }
  if (!corpusId || corpusId.includes('/')) {
    throw new VertexRagError('RAG_CORPUS_INVALID', 'RAG corpus id must be an id or exact resource name')
  }
  return `${expectedPrefix}${corpusId}`
}

function normalizeContext(raw: RawVertexRagContext): VertexRagContext {
  if (typeof raw.sourceUri !== 'string' || typeof raw.sourceDisplayName !== 'string' || typeof raw.text !== 'string') {
    throw new VertexRagError('RAG_CONTEXT_INVALID', 'Vertex RAG context is missing source or text fields')
  }
  return {
    sourceUri: raw.sourceUri,
    sourceDisplayName: raw.sourceDisplayName,
    text: raw.text,
    ...(raw.chunk !== undefined ? { chunk: raw.chunk } : {}),
    ...(typeof raw.score === 'number' ? { score: raw.score } : {}),
  }
}

function metadataFilterFor(request: RagBackendRequest): string | undefined {
  const clauses: string[] = []
  if (request.corpusKind === 'OFFICIAL') {
    if (request.sourceFamilies?.length) {
      const familyClauses = request.sourceFamilies.map((family) => `source_family == ${JSON.stringify(family)}`)
      clauses.push(familyClauses.length === 1 ? familyClauses[0] : `(${familyClauses.join(' || ')})`)
    }
    if (request.asOf) {
      clauses.push(`published_or_data_date <= ${JSON.stringify(request.asOf)}`)
    }
  } else if (request.documentType) {
    clauses.push(`document_type == ${JSON.stringify(request.documentType)}`)
  }
  return clauses.length ? clauses.join(' && ') : undefined
}

export function createVertexRagBackend(options: VertexRagBackendOptions): RagBackend {
  if (!options.projectId) throw new VertexRagError('RAG_PROJECT_REQUIRED', 'GCP project id is required')
  if (options.region !== GCP_LOCATIONS.rag) {
    throw new VertexRagError('RAG_REGION_NOT_ALLOWED', `RAG retrieval is pinned to ${GCP_LOCATIONS.rag}`)
  }
  const fetchImpl = options.fetchImpl ?? fetch

  return async (request) => {
    if (request.corpusKind === 'PROJECT' && (!request.ragFileIds || request.ragFileIds.length === 0)) {
      throw new VertexRagError('RAG_FILE_SCOPE_MISSING', 'project retrieval requires server-resolved RAG file ids')
    }
    if (request.limit < 1) throw new VertexRagError('RAG_LIMIT_INVALID', 'retrieval limit must be positive')
    const token = await options.accessToken()
    if (!token) throw new VertexRagError('RAG_AUTH_TOKEN_MISSING', 'ADC did not return an access token')

    const resource = corpusResource(options.projectId, options.region, request.corpusId)
    const endpoint = `https://${options.region}-aiplatform.googleapis.com/v1beta1/projects/${options.projectId}/locations/${options.region}:retrieveContexts`
    const ragResource: { ragCorpus: string; ragFileIds?: string[] } = { ragCorpus: resource }
    if (request.ragFileIds?.length) ragResource.ragFileIds = [...request.ragFileIds]
    const metadataFilter = metadataFilterFor(request)

    const response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        vertexRagStore: { ragResources: [ragResource] },
        query: {
          text: request.query,
          ragRetrievalConfig: {
            topK: request.limit,
            ...(metadataFilter ? { filter: { metadataFilter } } : {}),
          },
        },
      }),
    })
    if (!response.ok) {
      throw new VertexRagError('RAG_HTTP_ERROR', `retrieveContexts returned HTTP ${response.status}`, response.status)
    }

    const payload = await response.json() as VertexRetrieveContextsResponse
    const contexts = payload.contexts?.contexts ?? []
    return contexts.map((raw) => {
      const context = normalizeContext(raw)
      const mapped = options.mapContext(context, request)
      if (!mapped) {
        throw new VertexRagError(
          'RAG_CONTEXT_MAPPING_MISSING',
          'retrieved context cannot be mapped to an authoritative document revision and anchor',
        )
      }
      return mapped
    })
  }
}