import { RAG_RANKER, RAG_REGION } from './config'
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
  region: typeof RAG_REGION
  accessToken: () => Promise<string>
  mapContext: (context: VertexRagContext, request: RagBackendRequest) => RagHit | null
  fetchImpl?: typeof fetch
  timeoutMs?: number
}

const DEFAULT_RAG_TIMEOUT_MS = 5000

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
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

export function normalizeVertexRagContext(raw: unknown): VertexRagContext {
  const value = record(raw)
  if (
    !value
    || typeof value.sourceUri !== 'string'
    || !value.sourceUri
    || typeof value.sourceDisplayName !== 'string'
    || !value.sourceDisplayName
    || typeof value.text !== 'string'
  ) {
    throw new VertexRagError('RAG_PROVIDER_PROTOCOL_ERROR', 'Vertex RAG context is missing required source or text fields')
  }
  if (value.score !== undefined && (typeof value.score !== 'number' || !Number.isFinite(value.score))) {
    throw new VertexRagError('RAG_PROVIDER_PROTOCOL_ERROR', 'Vertex RAG context score must be a finite number')
  }
  return {
    sourceUri: value.sourceUri,
    sourceDisplayName: value.sourceDisplayName,
    text: value.text,
    ...(value.chunk !== undefined ? { chunk: value.chunk } : {}),
    ...(typeof value.score === 'number' ? { score: value.score } : {}),
  }
}

function rawVertexRagContexts(payload: unknown): unknown[] {
  const root = record(payload)
  const envelope = root ? record(root.contexts) : null
  if (!envelope || !Array.isArray(envelope.contexts)) {
    throw new VertexRagError(
      'RAG_PROVIDER_PROTOCOL_ERROR',
      'Vertex retrieveContexts 2xx response is missing contexts.contexts array',
    )
  }
  return envelope.contexts
}

export interface VertexRagRequestSpec {
  ragCorpus: string
  ragFileIds?: readonly string[]
  query: string
  topK: number
  metadataFilter?: string
}

export function vertexRagEndpoint(projectId: string, region: string): string {
  return `https://${region}-aiplatform.googleapis.com/v1beta1/projects/${projectId}/locations/${region}:retrieveContexts`
}

export function buildVertexRagRequest(spec: VertexRagRequestSpec): Record<string, unknown> {
  const ragResource: { ragCorpus: string; ragFileIds?: string[] } = { ragCorpus: spec.ragCorpus }
  if (spec.ragFileIds?.length) ragResource.ragFileIds = [...spec.ragFileIds]
  return {
    vertexRagStore: { ragResources: [ragResource] },
    query: {
      text: spec.query,
      ragRetrievalConfig: {
        topK: spec.topK,
        ...(spec.metadataFilter ? { filter: { metadataFilter: spec.metadataFilter } } : {}),
      },
    },
  }
}

export function buildOfficialMetadataFilter(sourceFamilies: readonly string[], asOf: string): string | undefined {
  const clauses: string[] = []
  if (sourceFamilies.length) {
    const familyClauses = sourceFamilies.map((family) => `source_family == ${JSON.stringify(family)}`)
    clauses.push(familyClauses.length === 1 ? familyClauses[0] : `(${familyClauses.join(' || ')})`)
  }
  if (asOf) clauses.push(`published_or_data_date <= ${JSON.stringify(asOf)}`)
  return clauses.length ? clauses.join(' && ') : undefined
}

export function parseVertexRagContexts(payload: unknown): VertexRagContext[] {
  return rawVertexRagContexts(payload).map(normalizeVertexRagContext)
}

export interface VertexRankingRequestSpec {
  modelId: string
  query: string
  contexts: readonly VertexRagContext[]
}

export function vertexRankingEndpoint(projectId: string, region: string): string {
  return `https://discoveryengine.googleapis.com/v1/projects/${projectId}/locations/${region}/rankingConfigs/default_ranking_config:rank`
}

export function buildVertexRankingRequest(spec: VertexRankingRequestSpec): Record<string, unknown> {
  return {
    model: spec.modelId,
    query: spec.query,
    records: spec.contexts.map((context, index) => ({
      id: `context-${index}`,
      title: context.sourceDisplayName,
      content: context.text,
    })),
    topN: spec.contexts.length,
  }
}

export function parseVertexRankingResponse(
  payload: unknown,
  contexts: readonly VertexRagContext[],
): VertexRagContext[] {
  const root = record(payload)
  const rows = root?.records
  if (!Array.isArray(rows) || rows.length !== contexts.length) {
    throw new VertexRagError(
      'RAG_RERANK_PROTOCOL_ERROR',
      `Ranking API must return exactly ${contexts.length} records`,
    )
  }

  const expectedIds = new Set(contexts.map((_context, index) => `context-${index}`))
  const observedIds = new Set<string>()
  return rows.map((raw) => {
    const row = record(raw)
    if (!row) {
      throw new VertexRagError('RAG_RERANK_PROTOCOL_ERROR', 'Ranking API record must be an object')
    }
    const id = row.id
    const score = row.score
    if (typeof id !== 'string' || !expectedIds.has(id) || observedIds.has(id)) {
      throw new VertexRagError('RAG_RERANK_PROTOCOL_ERROR', 'Ranking API returned an unknown or duplicate record id')
    }
    const index = Number(id.slice('context-'.length))
    const source = contexts[index]
    if (!source || typeof score !== 'number' || !Number.isFinite(score)) {
      throw new VertexRagError('RAG_RERANK_PROTOCOL_ERROR', 'Ranking API record score must be finite')
    }
    if (row.title !== source.sourceDisplayName || row.content !== source.text) {
      throw new VertexRagError('RAG_RERANK_PROTOCOL_ERROR', 'Ranking API record content differs from the requested context')
    }
    observedIds.add(id)
    return { ...source, score }
  })
}

function createRetrievalSignal(timeoutMs: number, callerSignal?: AbortSignal): {
  signal: AbortSignal
  cleanup: () => void
  timedOut: () => boolean
} {
  const controller = new AbortController()
  let timedOut = false
  const onCallerAbort = () => controller.abort(callerSignal?.reason)

  if (callerSignal?.aborted) onCallerAbort()
  else callerSignal?.addEventListener('abort', onCallerAbort, { once: true })

  const timer = setTimeout(() => {
    timedOut = true
    controller.abort(new Error('Vertex RAG retrieval deadline exceeded'))
  }, timeoutMs)

  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    cleanup: () => {
      clearTimeout(timer)
      callerSignal?.removeEventListener('abort', onCallerAbort)
    },
  }
}

async function awaitWithSignal<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) throw signal.reason ?? new Error('operation aborted')
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(signal.reason ?? new Error('operation aborted'))
    signal.addEventListener('abort', onAbort, { once: true })
    operation.then(
      (value) => {
        signal.removeEventListener('abort', onAbort)
        resolve(value)
      },
      (error: unknown) => {
        signal.removeEventListener('abort', onAbort)
        reject(error)
      },
    )
  })
}

function metadataFilterFor(request: RagBackendRequest): string | undefined {
  const clauses: string[] = []
  if (request.corpusKind === 'OFFICIAL') {
    // Imported RagFile metadata is provider-generated. A pinned file fence is
    // the authoritative routing boundary when the registry has real file IDs.
    if (request.ragFileIds?.length) return undefined
    return buildOfficialMetadataFilter(request.sourceFamilies ?? [], request.asOf ?? '')
  } else if (request.documentType) {
    clauses.push(`document_type == ${JSON.stringify(request.documentType)}`)
  }
  return clauses.length ? clauses.join(' && ') : undefined
}

export function createVertexRagBackend(options: VertexRagBackendOptions): RagBackend {
  if (!options.projectId) throw new VertexRagError('RAG_PROJECT_REQUIRED', 'GCP project id is required')
  if (options.region !== RAG_REGION) {
    throw new VertexRagError('RAG_REGION_NOT_ALLOWED', `RAG retrieval is pinned to ${RAG_REGION}`)
  }
  const fetchImpl = options.fetchImpl ?? fetch
  const timeoutMs = options.timeoutMs ?? DEFAULT_RAG_TIMEOUT_MS
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new VertexRagError('RAG_TIMEOUT_INVALID', 'RAG retrieval timeout must be a positive finite number')
  }

  return async (request) => {
    if (request.corpusKind === 'PROJECT' && (!request.ragFileIds || request.ragFileIds.length === 0)) {
      throw new VertexRagError('RAG_FILE_SCOPE_MISSING', 'project retrieval requires server-resolved RAG file ids')
    }
    if (request.limit < 1) throw new VertexRagError('RAG_LIMIT_INVALID', 'retrieval limit must be positive')
    if (request.signal?.aborted) throw new VertexRagError('RAG_CANCELLED', 'retrieveContexts was cancelled by the caller')

    const retrievalSignal = createRetrievalSignal(timeoutMs, request.signal)
    try {
      const token = await awaitWithSignal(options.accessToken(), retrievalSignal.signal)
      if (!token) throw new VertexRagError('RAG_AUTH_TOKEN_MISSING', 'ADC did not return an access token')

      const resource = corpusResource(options.projectId, options.region, request.corpusId)
      const endpoint = vertexRagEndpoint(options.projectId, options.region)
      const metadataFilter = metadataFilterFor(request)

      const response = await fetchImpl(endpoint, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(buildVertexRagRequest({
          ragCorpus: resource,
          ragFileIds: request.ragFileIds,
          query: request.query,
          topK: request.limit,
          ...(metadataFilter ? { metadataFilter } : {}),
        })),
        signal: retrievalSignal.signal,
      })
      if (!response.ok) {
        throw new VertexRagError('RAG_HTTP_ERROR', `retrieveContexts returned HTTP ${response.status}`, response.status)
      }

      let payload: unknown
      try {
        payload = await response.json()
      } catch (error) {
        if (retrievalSignal.timedOut()) {
          throw new VertexRagError('RAG_TIMEOUT', `retrieveContexts exceeded ${timeoutMs}ms deadline`)
        }
        if (request.signal?.aborted) {
          throw new VertexRagError('RAG_CANCELLED', 'retrieveContexts was cancelled by the caller')
        }
        throw new VertexRagError('RAG_PROVIDER_PROTOCOL_ERROR', `Vertex retrieveContexts returned invalid JSON: ${String(error)}`)
      }

      const contexts = parseVertexRagContexts(payload)
      if (contexts.length > request.limit) {
        throw new VertexRagError(
          'RAG_RESULT_LIMIT_EXCEEDED',
          `Vertex returned ${contexts.length} contexts for requested limit ${request.limit}`,
        )
      }

      let rankedContexts = contexts
      if (contexts.length > 0) {
        const rankingResponse = await fetchImpl(vertexRankingEndpoint(options.projectId, options.region), {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
            'X-Goog-User-Project': options.projectId,
          },
          body: JSON.stringify(buildVertexRankingRequest({
            modelId: RAG_RANKER.id,
            query: request.query,
            contexts,
          })),
          signal: retrievalSignal.signal,
        })
        if (!rankingResponse.ok) {
          throw new VertexRagError(
            'RAG_RERANK_HTTP_ERROR',
            `Ranking API returned HTTP ${rankingResponse.status}`,
            rankingResponse.status,
          )
        }
        let rankingPayload: unknown
        try {
          rankingPayload = await rankingResponse.json()
        } catch (error) {
          if (retrievalSignal.timedOut()) {
            throw new VertexRagError('RAG_TIMEOUT', `RAG retrieval exceeded ${timeoutMs}ms deadline`)
          }
          if (request.signal?.aborted) {
            throw new VertexRagError('RAG_CANCELLED', 'retrieveContexts was cancelled by the caller')
          }
          throw new VertexRagError('RAG_RERANK_PROTOCOL_ERROR', `Ranking API returned invalid JSON: ${String(error)}`)
        }
        rankedContexts = parseVertexRankingResponse(rankingPayload, contexts)
      }

      return rankedContexts.map((raw) => {
        const context = raw
        const mapped = options.mapContext(context, request)
        if (!mapped) {
          throw new VertexRagError(
            'RAG_CONTEXT_MAPPING_MISSING',
            'retrieved context cannot be mapped to an authoritative document revision and anchor',
          )
        }
        return mapped
      })
    } catch (error) {
      if (error instanceof VertexRagError) throw error
      if (retrievalSignal.timedOut()) {
        throw new VertexRagError('RAG_TIMEOUT', `retrieveContexts exceeded ${timeoutMs}ms deadline`)
      }
      if (request.signal?.aborted) {
        throw new VertexRagError('RAG_CANCELLED', 'retrieveContexts was cancelled by the caller')
      }
      throw error
    } finally {
      retrievalSignal.cleanup()
    }
  }
}
