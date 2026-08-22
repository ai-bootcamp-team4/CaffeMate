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

function normalizeContext(raw: unknown): VertexRagContext {
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

function parseContexts(payload: unknown): unknown[] {
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
              ranking: {
                rankService: { modelName: RAG_RANKER.id },
              },
              ...(metadataFilter ? { filter: { metadataFilter } } : {}),
            },
          },
        }),
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

      const contexts = parseContexts(payload)
      if (contexts.length > request.limit) {
        throw new VertexRagError(
          'RAG_RESULT_LIMIT_EXCEEDED',
          `Vertex returned ${contexts.length} contexts for requested limit ${request.limit}`,
        )
      }
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