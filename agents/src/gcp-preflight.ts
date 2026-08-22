import { RAG_RANKER } from '../../rag/src/config'
import { GCP_LOCATIONS } from './registry'
import { AGENT_RUNTIME_CLASS_METHODS } from './runtime-contract'

const OFFICIAL_CORPUS_DISPLAY_NAME = 'caffemate-official-v1'
const EMBEDDING_MODEL_ID = 'text-multilingual-embedding-002'
const RUNTIME_DISPLAY_NAME = 'caffemate-agents'

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
    | 'embedding'
    | 'rag-retrieval'
    | 'reranker'
    | 'generation-model'
    | 'agent-runtime'
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
  runtimeResource?: string
  checks: GcpPreflightCheck[]
}

export interface GcpPreflightOptions {
  projectId: string
  runtimeRegion: typeof GCP_LOCATIONS.runtime
  generationRegion: typeof GCP_LOCATIONS.generation
  ragRegion: typeof GCP_LOCATIONS.rag
  embeddingRegion: typeof GCP_LOCATIONS.embedding
  approvedModelId?: string
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

interface ReasoningEngineRow {
  name?: string
  displayName?: string
  spec?: {
    classMethods?: Array<{
      name?: string
      api_mode?: string
    }>
  }
}

function pass(name: GcpPreflightCheck['name'], code: string, detail?: string): GcpPreflightCheck {
  return { name, ok: true, code, ...(detail ? { detail } : {}) }
}

function fail(name: GcpPreflightCheck['name'], code: string, detail?: string): GcpPreflightCheck {
  return { name, ok: false, code, ...(detail ? { detail } : {}) }
}

function runtimeClassMethodMismatch(runtime: ReasoningEngineRow): string | null {
  const actual = new Set(
    (runtime.spec?.classMethods ?? [])
      .filter((method) => typeof method.name === 'string' && typeof method.api_mode === 'string')
      .map((method) => `${method.name}:${method.api_mode}`),
  )
  const expected = AGENT_RUNTIME_CLASS_METHODS.map((method) => `${method.name}:${method.api_mode}`)
  const expectedSet = new Set<string>(expected)
  for (const method of expected) {
    if (!actual.has(method)) return method
  }
  if (actual.size !== expected.length) {
    return [...actual].find((method) => !expectedSet.has(method)) ?? 'unexpected class method'
  }
  return null
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
}

function regionalBase(projectId: string, region: string): string {
  return `https://${region}-aiplatform.googleapis.com/v1/projects/${projectId}/locations/${region}`
}

function generationEndpoint(projectId: string, region: typeof GCP_LOCATIONS.generation, modelId: string): string {
  const host = region === 'global' ? 'aiplatform.googleapis.com' : `${region}-aiplatform.googleapis.com`
  return `https://${host}/v1/projects/${projectId}/locations/${region}/publishers/google/models/${encodeURIComponent(modelId)}:generateContent`
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
  const runtimeBase = regionalBase(options.projectId, options.runtimeRegion)
  let ragCorpusResource: string | undefined
  let activeRagFileCount = 0

  const corporaResponse = await request(fetchImpl, token, `${ragBase}/ragCorpora?pageSize=100`)
  if (!corporaResponse.ok) {
    checks.push(fail('rag-corpus', 'RAG_CORPUS_LIST_FAILED', `HTTP ${corporaResponse.status}`))
  } else {
    const payload = await corporaResponse.json() as { ragCorpora?: RagCorpusRow[] }
    const matches = (payload.ragCorpora ?? []).filter((row) => row.displayName === OFFICIAL_CORPUS_DISPLAY_NAME)
    if (matches.length !== 1 || !matches[0]?.name) {
      checks.push(fail('rag-corpus', 'RAG_CORPUS_NOT_FOUND'))
    } else {
      const corpus = matches[0]
      const expectedEmbedding = `projects/${options.projectId}/locations/${options.embeddingRegion}/publishers/google/models/${EMBEDDING_MODEL_ID}`
      const actualEmbedding = corpus.vectorDbConfig?.ragEmbeddingModelConfig?.vertexPredictionEndpoint?.endpoint
      if (corpus.corpusStatus?.state !== 'ACTIVE') {
        checks.push(fail('rag-corpus', 'RAG_CORPUS_NOT_ACTIVE', corpus.corpusStatus?.state ?? 'UNKNOWN'))
      } else if (actualEmbedding !== expectedEmbedding) {
        checks.push(fail('rag-corpus', 'RAG_EMBEDDING_MODEL_MISMATCH', actualEmbedding ?? 'MISSING'))
      } else {
        ragCorpusResource = corpus.name
        checks.push(pass('rag-corpus', 'RAG_CORPUS_OK', corpus.name))
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
      const filesResponse = await request(
        fetchImpl,
        token,
        `${ragBase}/ragCorpora/${encodeURIComponent(corpusId)}/ragFiles?pageSize=100`,
      )
      if (!filesResponse.ok) {
        checks.push(fail('rag-files', 'RAG_FILE_LIST_FAILED', `HTTP ${filesResponse.status}`))
      } else {
        const payload = await filesResponse.json() as { ragFiles?: RagFileRow[] }
        const activeFiles = (payload.ragFiles ?? []).filter((file) => file.name && file.fileStatus?.state === 'ACTIVE')
        activeRagFileCount = activeFiles.length
        checks.push(activeFiles.length > 0
          ? pass('rag-files', 'RAG_FILES_OK', String(activeFiles.length))
          : fail('rag-files', 'RAG_CORPUS_EMPTY'))
      }
    }
  }

  const embeddingEndpoint = `${embeddingBase}/publishers/google/models/${EMBEDDING_MODEL_ID}:predict`
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
      ? pass('embedding', 'EMBEDDING_PREFLIGHT_OK', EMBEDDING_MODEL_ID)
      : fail('embedding', 'EMBEDDING_RESPONSE_INVALID'))
  }

  if (!ragCorpusResource) {
    checks.push(fail('rag-retrieval', 'RAG_RETRIEVAL_BLOCKED_BY_CORPUS'))
  } else {
    const retrievalResponse = await request(fetchImpl, token, `${ragBase}:retrieveContexts`, {
      method: 'POST',
      body: JSON.stringify({
        vertexRagStore: { ragResources: [{ ragCorpus: ragCorpusResource }] },
        query: { text: 'caffemate preflight', ragRetrievalConfig: { topK: 1 } },
      }),
    })
    checks.push(retrievalResponse.ok
      ? pass('rag-retrieval', 'RAG_RETRIEVAL_OK')
      : fail('rag-retrieval', 'RAG_RETRIEVAL_FAILED', `HTTP ${retrievalResponse.status}`))
  }

  if (!ragCorpusResource) {
    checks.push(fail('reranker', 'RERANKER_BLOCKED_BY_CORPUS'))
  } else if (activeRagFileCount === 0) {
    checks.push(fail('reranker', 'RERANKER_BLOCKED_BY_EMPTY_CORPUS'))
  } else {
    const rerankerResponse = await request(fetchImpl, token, `${ragBase}:retrieveContexts`, {
      method: 'POST',
      body: JSON.stringify({
        vertexRagStore: { ragResources: [{ ragCorpus: ragCorpusResource }] },
        query: {
          text: '커피전문점 영업신고',
          ragRetrievalConfig: {
            topK: 2,
            ranking: {
              rankService: { modelName: RAG_RANKER.id },
            },
          },
        },
      }),
    })
    if (!rerankerResponse.ok) {
      checks.push(fail('reranker', 'RERANKER_PREFLIGHT_FAILED', `HTTP ${rerankerResponse.status}`))
    } else {
      const rerankerPayload = await rerankerResponse.json() as {
        contexts?: { contexts?: unknown[] }
      }
      const contexts = rerankerPayload.contexts?.contexts
      checks.push(Array.isArray(contexts) && contexts.length > 0
        ? pass('reranker', 'RERANKER_PREFLIGHT_OK', RAG_RANKER.id)
        : fail('reranker', 'RERANKER_RESPONSE_INVALID'))
    }
  }

  if (!options.approvedModelId) {
    checks.push(fail('generation-model', 'MODEL_NOT_APPROVED'))
  } else {
    const generationResponse = await request(
      fetchImpl,
      token,
      generationEndpoint(options.projectId, options.generationRegion, options.approvedModelId),
      {
        method: 'POST',
        body: JSON.stringify({
          contents: [{ role: 'user', parts: [{ text: 'Return exactly {"ok":true}.' }] }],
          generationConfig: {
            candidateCount: 1,
            responseMimeType: 'application/json',
            maxOutputTokens: 256,
          },
        }),
      },
    )
    if (!generationResponse.ok) {
      checks.push(fail('generation-model', 'GENERATION_PREFLIGHT_FAILED', `HTTP ${generationResponse.status}`))
    } else {
      const generationPayload = await generationResponse.json() as {
        candidates?: Array<{ content?: { parts?: Array<{ text?: string }> }; finishReason?: string }>
      }
      const candidate = generationPayload.candidates?.[0]
      const text = candidate?.content?.parts?.[0]?.text
      checks.push(typeof text === 'string' && text.length > 0 && candidate?.finishReason === 'STOP'
        ? pass('generation-model', 'GENERATION_PREFLIGHT_OK', options.approvedModelId)
        : fail('generation-model', 'GENERATION_RESPONSE_INVALID'))
    }
  }

  const runtimeResponse = await request(fetchImpl, token, `${runtimeBase}/reasoningEngines?pageSize=100`)
  let runtimeResource: string | undefined
  if (!runtimeResponse.ok) {
    checks.push(fail('agent-runtime', 'AGENT_RUNTIME_LIST_FAILED', `HTTP ${runtimeResponse.status}`))
  } else {
    const payload = await runtimeResponse.json() as { reasoningEngines?: ReasoningEngineRow[] }
    const matches = (payload.reasoningEngines ?? []).filter((row) => row.displayName === RUNTIME_DISPLAY_NAME)
    if (matches.length !== 1 || !matches[0]?.name) {
      checks.push(fail('agent-runtime', 'AGENT_RUNTIME_NOT_DEPLOYED'))
    } else {
      runtimeResource = matches[0].name
      const runtimeId = runtimeResource.split('/').at(-1)
      if (!runtimeId) {
        checks.push(fail('agent-runtime', 'AGENT_RUNTIME_RESOURCE_INVALID', runtimeResource))
      } else {
        const runtimeGetResponse = await request(
          fetchImpl,
          token,
          `${runtimeBase}/reasoningEngines/${encodeURIComponent(runtimeId)}`,
        )
        if (!runtimeGetResponse.ok) {
          checks.push(fail('agent-runtime', 'AGENT_RUNTIME_GET_FAILED', `HTTP ${runtimeGetResponse.status}`))
        } else {
          const runtime = await runtimeGetResponse.json() as ReasoningEngineRow
          const mismatch = runtimeClassMethodMismatch(runtime)
          checks.push(mismatch
            ? fail('agent-runtime', 'AGENT_RUNTIME_CLASS_METHOD_MISMATCH', mismatch)
            : pass('agent-runtime', 'AGENT_RUNTIME_OK', runtimeResource))
        }
      }
    }
  }

  return {
    ok: checks.every((check) => check.ok),
    projectId: options.projectId,
    runtimeRegion: options.runtimeRegion,
    generationRegion: options.generationRegion,
    ragRegion: options.ragRegion,
    embeddingRegion: options.embeddingRegion,
    ...(ragCorpusResource ? { ragCorpusResource } : {}),
    ...(runtimeResource ? { runtimeResource } : {}),
    checks,
  }
}
