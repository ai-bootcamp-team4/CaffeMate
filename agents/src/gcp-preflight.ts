import {
  buildOfficialMetadataFilter,
  buildVertexRagRequest,
  buildVertexRankingRequest,
  parseVertexRagContexts,
  parseVertexRankingResponse,
  vertexRagEndpoint,
  vertexRankingEndpoint,
} from '../../rag/src/vertex-rag-backend'
import { AGENT_MODEL, GCP_LOCATIONS, TASK_REGISTRY } from './registry'
import {
  type GcpMcpRuntimePin,
  type GcpRuntimePin,
  validateMcpRuntimePin,
  validateRuntimePin,
  verifyAgentRuntime,
  verifyMcpRuntime,
} from './gcp-runtime-preflight'
import { validGcsSourcePin, verifyPinnedSourceObjects } from './gcp-source-object-preflight'
import {
  buildVertexGenerationRequest,
  parseVertexGenerationResponse,
  vertexGenerationEndpoint,
} from './vertex-generation-contract'

const GENERATION_PREFLIGHT_MAX_OUTPUT_TOKENS = Math.max(
  ...Object.values(TASK_REGISTRY).map((registration) => registration.maxOutputTokens),
)
const GENERATION_PREFLIGHT_RESPONSE_SCHEMA = Object.freeze({
  type: 'object',
  additionalProperties: false,
  required: ['ok'],
  properties: {
    ok: { type: 'boolean' },
  },
} as const)

export class GcpPreflightError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'GcpPreflightError'
  }
}

export interface GcpPreflightCheck {
  name:
    | 'auth'
    | 'rag-corpus'
    | 'rag-files'
    | 'rag-source-objects'
    | 'embedding'
    | 'rag-retrieval'
    | 'reranker'
    | 'generation-model'
    | 'agent-runtime'
    | 'mcp-runtime'
  ok: boolean
  code: string
  detail?: string
}

export interface GcpPreflightResult {
  ok: boolean
  projectId: string
  runtimeRegion: typeof GCP_LOCATIONS.runtime
  generationRegion: typeof GCP_LOCATIONS.generation
  ragRegion: typeof GCP_LOCATIONS.rag
  embeddingRegion: typeof GCP_LOCATIONS.embedding
  ragCorpusResource?: string
  ragFileResources?: string[]
  embeddingModelId?: string
  rerankerId?: string
  generationModelId?: string
  runtimeResource?: string
  runtimeImageUri?: string
  checks: GcpPreflightCheck[]
}

export type { GcpRuntimePin, GcpMcpRuntimePin } from './gcp-runtime-preflight'

export interface GcpRagSourcePin {
  sourceFamily: string
  sourceDate: string
  sourceUri: string
  gcsObjectGeneration: string
  ragFileResourceName: string
}

export interface GcpRagPin {
  corpusResourceName: string
  ragFileResourceNames: readonly string[]
  embeddingModelId: string
  rerankerId: string
  sourceRevisions: readonly GcpRagSourcePin[]
}

export interface GcpPreflightOptions {
  projectId: string
  runtimeRegion: typeof GCP_LOCATIONS.runtime
  generationRegion: typeof GCP_LOCATIONS.generation
  ragRegion: typeof GCP_LOCATIONS.rag
  embeddingRegion: typeof GCP_LOCATIONS.embedding
  approvedModelId?: string
  runtimePin: GcpRuntimePin
  mcpPin: GcpMcpRuntimePin
  ragPin: GcpRagPin
  accessToken: () => Promise<string>
  fetchImpl?: typeof fetch
}

interface RagCorpusRow {
  name?: string
  displayName?: string
  corpusStatus?: { state?: string }
  vectorDbConfig?: {
    ragEmbeddingModelConfig?: {
      vertexPredictionEndpoint?: { endpoint?: string }
    }
  }
}

interface RagFileRow {
  name?: string
  fileStatus?: { state?: string }
}

type RagFileListResult =
  | { ok: true; files: RagFileRow[] }
  | { ok: false; code: 'RAG_FILE_LIST_FAILED' | 'RAG_FILE_LIST_RESPONSE_INVALID'; detail: string }

interface RagCorpusIdentity {
  project: string
  location: string
  corpusId: string
}

function ragCorpusIdentity(resourceName: string): RagCorpusIdentity | null {
  const match = /^projects\/([^/]+)\/locations\/([^/]+)\/ragCorpora\/([^/]+)$/.exec(resourceName)
  if (!match) return null
  const [, project, location, corpusId] = match
  if (!project || !location || !corpusId) return null
  return { project, location, corpusId }
}

function ragFileId(resourceName: string, corpusResourceName: string): string | null {
  if (!resourceName.startsWith(`${corpusResourceName}/ragFiles/`)) return null
  const fileId = resourceName.slice(`${corpusResourceName}/ragFiles/`.length)
  return fileId && !fileId.includes('/') ? fileId : null
}

function ragFileChunkId(context: { chunk?: unknown }): string | null {
  if (!context.chunk || typeof context.chunk !== 'object' || Array.isArray(context.chunk)) return null
  const value = context.chunk as Record<string, unknown>
  return typeof value.fileId === 'string' && value.fileId ? value.fileId : null
}

function contextMatchesSourcePin(
  context: { sourceUri: string; chunk?: unknown },
  source: GcpRagSourcePin,
): boolean {
  return context.sourceUri === source.sourceUri
    && ragFileChunkId(context) === source.ragFileResourceName.split('/').at(-1)
}

function pass(name: GcpPreflightCheck['name'], code: string, detail?: string): GcpPreflightCheck {
  return { name, ok: true, code, ...(detail ? { detail } : {}) }
}

function fail(name: GcpPreflightCheck['name'], code: string, detail?: string): GcpPreflightCheck {
  return { name, ok: false, code, ...(detail ? { detail } : {}) }
}

function assertLocations(options: GcpPreflightOptions): void {
  if (options.runtimeRegion !== GCP_LOCATIONS.runtime) {
    throw new GcpPreflightError('GCP_RUNTIME_REGION_NOT_ALLOWED', `Agent Runtime is pinned to ${GCP_LOCATIONS.runtime}`)
  }
  if (options.generationRegion !== GCP_LOCATIONS.generation) {
    throw new GcpPreflightError('GCP_GENERATION_REGION_NOT_ALLOWED', `Gemini generation is pinned to ${GCP_LOCATIONS.generation}`)
  }
  if (options.ragRegion !== GCP_LOCATIONS.rag) {
    throw new GcpPreflightError('GCP_RAG_REGION_NOT_ALLOWED', `RAG Engine is pinned to ${GCP_LOCATIONS.rag}`)
  }
  if (options.embeddingRegion !== GCP_LOCATIONS.embedding) {
    throw new GcpPreflightError('GCP_EMBEDDING_REGION_NOT_ALLOWED', `embedding is pinned to ${GCP_LOCATIONS.embedding}`)
  }

  const corpus = ragCorpusIdentity(options.ragPin.corpusResourceName)
  if (!corpus || corpus.project !== options.projectId || corpus.location !== options.ragRegion) {
    throw new GcpPreflightError(
      'GCP_RAG_CORPUS_PIN_INVALID',
      'release corpus must belong to the requested project and RAG region',
    )
  }
  if (options.ragPin.ragFileResourceNames.length === 0
    || options.ragPin.ragFileResourceNames.some((resource) => ragFileId(resource, options.ragPin.corpusResourceName) === null)) {
    throw new GcpPreflightError('GCP_RAG_FILE_PIN_INVALID', 'release RAG files must belong to the pinned corpus')
  }
  if (options.ragPin.sourceRevisions.length === 0
    || options.ragPin.sourceRevisions.some((source) => {
      return !source.sourceFamily
        || !/^\d{4}-\d{2}-\d{2}$/.test(source.sourceDate)
        || !validGcsSourcePin(source)
        || ragFileId(source.ragFileResourceName, options.ragPin.corpusResourceName) === null
    })) {
    throw new GcpPreflightError('GCP_RAG_SOURCE_PIN_INVALID', 'release RAG source revisions must pin family/date/GCS generation/RagFile')
  }
  const sourceRagFiles = new Set(options.ragPin.sourceRevisions.map((source) => source.ragFileResourceName))
  if (sourceRagFiles.size !== options.ragPin.ragFileResourceNames.length
    || options.ragPin.ragFileResourceNames.some((resource) => !sourceRagFiles.has(resource))) {
    throw new GcpPreflightError('GCP_RAG_SOURCE_PIN_INVALID', 'source revision RagFiles must equal the pinned active file set')
  }

  if (!validateRuntimePin(options.projectId, options.runtimeRegion, options.runtimePin)) {
    throw new GcpPreflightError(
      'GCP_RUNTIME_PIN_INVALID',
      'release Runtime must belong to the requested project and Runtime region',
    )
  }
  if (!validateMcpRuntimePin(options.projectId, options.ragRegion, options.mcpPin)) {
    throw new GcpPreflightError(
      'GCP_MCP_RUNTIME_PIN_INVALID',
      'release MCP runtime must pin the Seoul caffemate-mcp service, source revision and immutable image',
    )
  }
}

function regionalBase(projectId: string, region: string): string {
  return `https://${region}-aiplatform.googleapis.com/v1/projects/${projectId}/locations/${region}`
}

async function request(fetchImpl: typeof fetch, token: string, url: string, init?: RequestInit): Promise<Response> {
  return fetchImpl(url, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
}

async function listAllRagFiles(
  fetchImpl: typeof fetch,
  token: string,
  listUrl: string,
): Promise<RagFileListResult> {
  const files: RagFileRow[] = []
  const seenPageTokens = new Set<string>()
  let pageToken: string | undefined

  while (true) {
    const response = await request(
      fetchImpl,
      token,
      `${listUrl}?pageSize=100${pageToken ? `&pageToken=${encodeURIComponent(pageToken)}` : ''}`,
    )
    if (!response.ok) {
      return { ok: false, code: 'RAG_FILE_LIST_FAILED', detail: `HTTP ${response.status}` }
    }

    const payload = await response.json() as { ragFiles?: unknown; nextPageToken?: unknown }
    if (payload.ragFiles !== undefined && !Array.isArray(payload.ragFiles)) {
      return { ok: false, code: 'RAG_FILE_LIST_RESPONSE_INVALID', detail: 'ragFiles must be an array' }
    }
    files.push(...((payload.ragFiles ?? []) as RagFileRow[]))

    if (payload.nextPageToken === undefined || payload.nextPageToken === '') {
      return { ok: true, files }
    }
    if (typeof payload.nextPageToken !== 'string' || seenPageTokens.has(payload.nextPageToken)) {
      return { ok: false, code: 'RAG_FILE_LIST_RESPONSE_INVALID', detail: 'nextPageToken is invalid or repeated' }
    }
    seenPageTokens.add(payload.nextPageToken)
    pageToken = payload.nextPageToken
  }
}

function parsePreflightContexts(
  payload: unknown,
  source: GcpRagSourcePin,
  limit: number,
): { ok: true; count: number } | { ok: false; detail: string } {
  try {
    const contexts = parseVertexRagContexts(payload)
    if (contexts.length === 0) return { ok: false, detail: 'no contexts returned' }
    if (contexts.length > limit) return { ok: false, detail: `returned ${contexts.length} contexts for topK ${limit}` }
    if (contexts.some((context) => !contextMatchesSourcePin(context, source))) {
      return { ok: false, detail: 'context escaped the pinned source URI or RagFile' }
    }
    return { ok: true, count: contexts.length }
  } catch (error) {
    return { ok: false, detail: error instanceof Error ? error.message : String(error) }
  }
}

export async function runGcpPreflight(options: GcpPreflightOptions): Promise<GcpPreflightResult> {
  if (!options.projectId) throw new GcpPreflightError('GCP_PROJECT_REQUIRED', 'GCP project id is required')
  assertLocations(options)

  const fetchImpl = options.fetchImpl ?? fetch
  const checks: GcpPreflightCheck[] = []
  const token = await options.accessToken()
  if (!token) throw new GcpPreflightError('GCP_ACCESS_TOKEN_UNRESOLVED', 'ADC did not return an access token')
  checks.push(pass('auth', 'GCP_AUTH_OK'))

  const ragBase = regionalBase(options.projectId, options.ragRegion)
  const embeddingBase = regionalBase(options.projectId, options.embeddingRegion)
  let ragCorpusResource: string | undefined
  let ragFileResources: string[] | undefined
  let activeRagFileCount = 0

  const pinnedCorpus = ragCorpusIdentity(options.ragPin.corpusResourceName)
  if (!pinnedCorpus) throw new GcpPreflightError('GCP_RAG_CORPUS_PIN_INVALID', options.ragPin.corpusResourceName)
  const corpusResponse = await request(
    fetchImpl,
    token,
    `${ragBase}/ragCorpora/${encodeURIComponent(pinnedCorpus.corpusId)}`,
  )
  if (!corpusResponse.ok) {
    checks.push(fail('rag-corpus', 'RAG_CORPUS_GET_FAILED', `HTTP ${corpusResponse.status}`))
  } else {
    const corpus = await corpusResponse.json() as RagCorpusRow
    const actualIdentity = corpus.name ? ragCorpusIdentity(corpus.name) : null
    if (!actualIdentity
      || actualIdentity.location !== pinnedCorpus.location
      || actualIdentity.corpusId !== pinnedCorpus.corpusId) {
      checks.push(fail('rag-corpus', 'RAG_CORPUS_RESOURCE_MISMATCH', corpus.name ?? 'MISSING'))
    } else {
      const expectedEmbedding = `projects/${options.projectId}/locations/${options.embeddingRegion}/publishers/google/models/${options.ragPin.embeddingModelId}`
      const actualEmbedding = corpus.vectorDbConfig?.ragEmbeddingModelConfig?.vertexPredictionEndpoint?.endpoint
      if (corpus.corpusStatus?.state !== 'ACTIVE') {
        checks.push(fail('rag-corpus', 'RAG_CORPUS_NOT_ACTIVE', corpus.corpusStatus?.state ?? 'UNKNOWN'))
      } else if (actualEmbedding !== expectedEmbedding) {
        checks.push(fail('rag-corpus', 'RAG_EMBEDDING_MODEL_MISMATCH', actualEmbedding ?? 'MISSING'))
      } else {
        ragCorpusResource = options.ragPin.corpusResourceName
        checks.push(pass('rag-corpus', 'RAG_CORPUS_OK', ragCorpusResource))
      }
    }
  }

  if (!ragCorpusResource) {
    checks.push(fail('rag-files', 'RAG_FILES_BLOCKED_BY_CORPUS'))
  } else {
    const corpusId = ragCorpusResource.split('/').at(-1)
    if (!corpusId) {
      checks.push(fail('rag-files', 'RAG_CORPUS_RESOURCE_INVALID', ragCorpusResource))
    } else {
      const filesResult = await listAllRagFiles(
        fetchImpl,
        token,
        `${ragBase}/ragCorpora/${encodeURIComponent(corpusId)}/ragFiles`,
      )
      if (!filesResult.ok) {
        checks.push(fail('rag-files', filesResult.code, filesResult.detail))
      } else {
        const activeFiles = filesResult.files.filter((file) => file?.name && file.fileStatus?.state === 'ACTIVE')
        activeRagFileCount = activeFiles.length
        const actualFileIds = new Set(
          activeFiles.map((file) => file.name?.split('/').at(-1)).filter((value): value is string => Boolean(value)),
        )
        const expectedFileIds = new Set(
          options.ragPin.ragFileResourceNames.map((resource) => resource.split('/').at(-1) as string),
        )
        const fileSetMatches = actualFileIds.size === expectedFileIds.size
          && [...expectedFileIds].every((fileId) => actualFileIds.has(fileId))
        if (activeFiles.length === 0) {
          checks.push(fail('rag-files', 'RAG_CORPUS_EMPTY'))
        } else if (!fileSetMatches) {
          checks.push(fail(
            'rag-files',
            'RAG_FILE_SET_MISMATCH',
            `expected ${[...expectedFileIds].sort().join(',')} found ${[...actualFileIds].sort().join(',')}`,
          ))
        } else {
          ragFileResources = [...options.ragPin.ragFileResourceNames]
          checks.push(pass('rag-files', 'RAG_FILES_OK', String(activeFiles.length)))
        }
      }
    }
  }

  if (!ragFileResources) {
    checks.push(fail('rag-source-objects', 'RAG_SOURCE_OBJECTS_BLOCKED_BY_FILES'))
  } else {
    checks.push(await verifyPinnedSourceObjects({ fetchImpl, token, sources: options.ragPin.sourceRevisions }))
  }

  const embeddingEndpoint = `${embeddingBase}/publishers/google/models/${options.ragPin.embeddingModelId}:predict`
  const embeddingResponse = await request(fetchImpl, token, embeddingEndpoint, {
    method: 'POST',
    body: JSON.stringify({
      instances: [{ content: 'CaffeMate regional embedding preflight' }],
      parameters: { outputDimensionality: 128 },
    }),
  })
  if (!embeddingResponse.ok) {
    checks.push(fail('embedding', 'EMBEDDING_PREFLIGHT_FAILED', `HTTP ${embeddingResponse.status}`))
  } else {
    const embeddingPayload = await embeddingResponse.json() as {
      predictions?: Array<{ embeddings?: { values?: unknown[] } }>
    }
    const values = embeddingPayload.predictions?.[0]?.embeddings?.values
    checks.push(Array.isArray(values) && values.length > 0
      ? pass('embedding', 'EMBEDDING_PREFLIGHT_OK', options.ragPin.embeddingModelId)
      : fail('embedding', 'EMBEDDING_RESPONSE_INVALID'))
  }

  const preflightSource = options.ragPin.sourceRevisions[0]
  const retrievalEndpoint = vertexRagEndpoint(options.projectId, options.ragRegion)
  const metadataFilter = preflightSource
    ? buildOfficialMetadataFilter([preflightSource.sourceFamily], preflightSource.sourceDate)
    : undefined

  if (!ragCorpusResource || !preflightSource) {
    checks.push(fail('rag-retrieval', 'RAG_RETRIEVAL_BLOCKED_BY_CORPUS'))
  } else {
    const retrievalResponse = await request(fetchImpl, token, retrievalEndpoint, {
      method: 'POST',
      body: JSON.stringify(buildVertexRagRequest({
        ragCorpus: ragCorpusResource,
        query: '커피전문점 영업신고',
        topK: 1,
        ...(metadataFilter ? { metadataFilter } : {}),
      })),
    })
    if (!retrievalResponse.ok) {
      checks.push(fail('rag-retrieval', 'RAG_RETRIEVAL_FAILED', `HTTP ${retrievalResponse.status}`))
    } else {
      const parsed = parsePreflightContexts(await retrievalResponse.json(), preflightSource, 1)
      checks.push(parsed.ok
        ? pass('rag-retrieval', 'RAG_RETRIEVAL_OK', String(parsed.count))
        : fail('rag-retrieval', 'RAG_RETRIEVAL_RESPONSE_INVALID', parsed.detail))
    }
  }

  if (!ragCorpusResource || !preflightSource) {
    checks.push(fail('reranker', 'RERANKER_BLOCKED_BY_CORPUS'))
  } else if (activeRagFileCount === 0) {
    checks.push(fail('reranker', 'RERANKER_BLOCKED_BY_EMPTY_CORPUS'))
  } else {
    const rerankRetrievalResponse = await request(fetchImpl, token, retrievalEndpoint, {
      method: 'POST',
      body: JSON.stringify(buildVertexRagRequest({
        ragCorpus: ragCorpusResource,
        query: '커피전문점 영업신고',
        topK: 2,
        ...(metadataFilter ? { metadataFilter } : {}),
      })),
    })
    if (!rerankRetrievalResponse.ok) {
      checks.push(fail('reranker', 'RERANKER_RETRIEVAL_FAILED', `HTTP ${rerankRetrievalResponse.status}`))
    } else {
      let contexts: ReturnType<typeof parseVertexRagContexts>
      try {
        contexts = parseVertexRagContexts(await rerankRetrievalResponse.json())
      } catch (error) {
        checks.push(fail(
          'reranker',
          'RERANKER_RETRIEVAL_RESPONSE_INVALID',
          error instanceof Error ? error.message : String(error),
        ))
        contexts = []
      }
      if (contexts.length === 0) {
        if (!checks.some((check) => check.name === 'reranker')) {
          checks.push(fail('reranker', 'RERANKER_RETRIEVAL_RESPONSE_INVALID', 'no contexts returned'))
        }
      } else if (contexts.length > 2 || contexts.some((context) => !contextMatchesSourcePin(context, preflightSource))) {
        checks.push(fail(
          'reranker',
          'RERANKER_RETRIEVAL_RESPONSE_INVALID',
          contexts.length > 2 ? `returned ${contexts.length} contexts for topK 2` : 'context escaped the pinned source URI or RagFile',
        ))
      } else {
        const rankingResponse = await request(
          fetchImpl,
          token,
          vertexRankingEndpoint(options.projectId, options.ragRegion),
          {
            method: 'POST',
            headers: { 'X-Goog-User-Project': options.projectId },
            body: JSON.stringify(buildVertexRankingRequest({
              modelId: options.ragPin.rerankerId,
              query: '커피전문점 영업신고',
              contexts,
            })),
          },
        )
        if (!rankingResponse.ok) {
          checks.push(fail('reranker', 'RERANKER_PREFLIGHT_FAILED', `HTTP ${rankingResponse.status}`))
        } else {
          try {
            parseVertexRankingResponse(await rankingResponse.json(), contexts)
            checks.push(pass('reranker', 'RERANKER_PREFLIGHT_OK', options.ragPin.rerankerId))
          } catch (error) {
            checks.push(fail(
              'reranker',
              'RERANKER_RESPONSE_INVALID',
              error instanceof Error ? error.message : String(error),
            ))
          }
        }
      }
    }
  }

  if (!options.approvedModelId) {
    checks.push(fail('generation-model', 'MODEL_NOT_APPROVED'))
  } else {
    const generationResponse = await request(
      fetchImpl,
      token,
      vertexGenerationEndpoint(options.projectId, options.generationRegion, options.approvedModelId),
      {
        method: 'POST',
        body: JSON.stringify(buildVertexGenerationRequest({
          systemInstruction: 'CaffeMate generation deployment preflight. Return only the requested JSON object.',
          userText: 'Return exactly {"ok":true}.',
          responseJsonSchema: GENERATION_PREFLIGHT_RESPONSE_SCHEMA,
          thinkingLevel: AGENT_MODEL.thinkingLevel,
          maxOutputTokens: GENERATION_PREFLIGHT_MAX_OUTPUT_TOKENS,
        })),
      },
    )
    if (!generationResponse.ok) {
      checks.push(fail('generation-model', 'GENERATION_PREFLIGHT_FAILED', `HTTP ${generationResponse.status}`))
    } else {
      try {
        const generationResult = parseVertexGenerationResponse(await generationResponse.json())
        checks.push(generationResult.kind === 'TEXT'
          ? pass('generation-model', 'GENERATION_PREFLIGHT_OK', options.approvedModelId)
          : fail('generation-model', 'GENERATION_RESPONSE_INVALID', generationResult.kind))
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error)
        checks.push(fail('generation-model', 'GENERATION_RESPONSE_INVALID', detail))
      }
    }
  }

  checks.push(await verifyMcpRuntime({
    projectId: options.projectId,
    pin: options.mcpPin,
    token,
    fetchImpl,
  }))

  const runtimeVerification = await verifyAgentRuntime({
    projectId: options.projectId,
    region: options.runtimeRegion,
    pin: options.runtimePin,
    token,
    fetchImpl,
  })
  checks.push(runtimeVerification.check)
  const { runtimeResource, runtimeImageUri } = runtimeVerification


  return {
    ok: checks.every((check) => check.ok),
    projectId: options.projectId,
    runtimeRegion: options.runtimeRegion,
    generationRegion: options.generationRegion,
    ragRegion: options.ragRegion,
    embeddingRegion: options.embeddingRegion,
    ...(ragCorpusResource ? { ragCorpusResource } : {}),
    ...(ragFileResources ? { ragFileResources } : {}),
    embeddingModelId: options.ragPin.embeddingModelId,
    rerankerId: options.ragPin.rerankerId,
    ...(options.approvedModelId ? { generationModelId: options.approvedModelId } : {}),
    ...(runtimeResource ? { runtimeResource } : {}),
    ...(runtimeImageUri ? { runtimeImageUri } : {}),
    checks,
  }
}
