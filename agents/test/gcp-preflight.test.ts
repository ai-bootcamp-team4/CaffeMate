import { describe, expect, it, vi } from 'vitest'
import { runGcpPreflight } from '../src/gcp-preflight'

const PROJECT_ID = 'proj-aj20-211200020328'
const RUNTIME_REGION = 'asia-northeast3'
const GENERATION_REGION = 'global'
const RAG_REGION = 'asia-northeast3'
const EMBEDDING_REGION = 'asia-northeast3'
const MODEL_ID = 'gemini-3.7-flash'
const RUNTIME_RESOURCE = `projects/${PROJECT_ID}/locations/${RUNTIME_REGION}/reasoningEngines/777`
const RUNTIME_IMAGE = `${RUNTIME_REGION}-docker.pkg.dev/${PROJECT_ID}/caffemate-agents/caffemate-agent-runtime@sha256:${'a'.repeat(64)}`

function successfulFetch() {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/ragCorpora?')) {
      return Response.json({
        ragCorpora: [{
          name: `projects/${PROJECT_ID}/locations/${RAG_REGION}/ragCorpora/5148740273991319552`,
          displayName: 'caffemate-official-v1',
          corpusStatus: { state: 'ACTIVE' },
          vectorDbConfig: {
            ragEmbeddingModelConfig: {
              vertexPredictionEndpoint: {
                endpoint: `projects/${PROJECT_ID}/locations/${EMBEDDING_REGION}/publishers/google/models/text-multilingual-embedding-002`,
              },
            },
          },
        }],
      })
    }
    if (url.includes('/ragFiles?')) {
      return Response.json({
        ragFiles: [{
          name: `projects/${PROJECT_ID}/locations/${RAG_REGION}/ragCorpora/5148740273991319552/ragFiles/1`,
          fileStatus: { state: 'ACTIVE' },
        }],
      })
    }
    if (url.includes('text-multilingual-embedding-002:predict')) {
      return Response.json({ predictions: [{ embeddings: { values: [0.1, 0.2] } }] })
    }
    if (url.endsWith(':retrieveContexts')) {
      const body = JSON.parse(String(init?.body)) as {
        query?: { ragRetrievalConfig?: { ranking?: { rankService?: { modelName?: string } } } }
      }
      const rankerModel = body.query?.ragRetrievalConfig?.ranking?.rankService?.modelName
      if (rankerModel) expect(rankerModel).toBe('semantic-ranker-default-004')
      return Response.json({
        contexts: {
          contexts: [{
            sourceUri: 'gs://caffemate-official/source.html',
            sourceDisplayName: 'source.html',
            text: '영업신고',
            score: 0.1,
          }],
        },
      })
    }
    if (url.includes(`${MODEL_ID}:generateContent`)) {
      return Response.json({
        candidates: [{ content: { parts: [{ text: '{"ok":true}' }] }, finishReason: 'STOP' }],
      })
    }
    if (url.includes('/reasoningEngines?')) {
      return Response.json({
        reasoningEngines: [{
          name: RUNTIME_RESOURCE,
          displayName: 'caffemate-agents',
        }],
      })
    }
    if (url.endsWith('/reasoningEngines/777')) {
      return Response.json({
        name: RUNTIME_RESOURCE,
        displayName: 'caffemate-agents',
        spec: {
          classMethods: [
            { name: 'async_create_session', api_mode: 'async' },
            { name: 'async_stream_query', api_mode: 'async_stream' },
            { name: 'async_delete_session', api_mode: 'async' },
          ],
          containerSpec: { imageUri: RUNTIME_IMAGE },
        },
      })
    }
    return Response.json({ error: { message: `unexpected ${url}` } }, { status: 404 })
  })
}

function options(fetchImpl = successfulFetch()) {
  return {
    projectId: PROJECT_ID,
    runtimeRegion: RUNTIME_REGION,
    generationRegion: GENERATION_REGION,
    ragRegion: RAG_REGION,
    embeddingRegion: EMBEDDING_REGION,
    approvedModelId: MODEL_ID,
    runtimePin: {
      resourceName: RUNTIME_RESOURCE,
      imageUri: RUNTIME_IMAGE,
    },
    accessToken: async () => 'adc-token',
    fetchImpl,
  } as const
}

describe('GCP deployment preflight', () => {
  it('passes all deployed GCP checks including the pinned Seoul RAG reranker', async () => {
    const fetchImpl = successfulFetch()
    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(true)
    expect(result.projectId).toBe(PROJECT_ID)
    expect(result.runtimeRegion).toBe(RUNTIME_REGION)
    expect(result.generationRegion).toBe(GENERATION_REGION)
    expect(result.ragRegion).toBe(RAG_REGION)
    expect(result.embeddingRegion).toBe(EMBEDDING_REGION)
    expect(result.ragCorpusResource).toContain('/ragCorpora/5148740273991319552')
    expect(result.runtimeResource).toContain('/reasoningEngines/777')
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'rag-files',
      ok: true,
      code: 'RAG_FILES_OK',
    }))
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'reranker',
      ok: true,
      code: 'RERANKER_PREFLIGHT_OK',
      detail: 'semantic-ranker-default-004',
    }))

    const urls = fetchImpl.mock.calls.map(([input]) => String(input))
    expect(urls.filter((url) => url.includes('/locations/global/'))).toEqual([
      `https://aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/global/publishers/google/models/${MODEL_ID}:generateContent`,
    ])
    expect(urls.filter((url) => url.includes('/reasoningEngines?')).every((url) => url.includes(RUNTIME_REGION))).toBe(true)
    expect(urls.filter((url) => url.includes('/ragCorpora?') || url.endsWith(':retrieveContexts')).every((url) => url.includes(RAG_REGION))).toBe(true)
    expect(urls.some((url) => url.includes('discoveryengine.googleapis.com'))).toBe(false)
  })

  it('fails closed when the deployed Runtime does not expose the required stream class method', async () => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/reasoningEngines/777')) {
        return Response.json({
          name: RUNTIME_RESOURCE,
          displayName: 'caffemate-agents',
          spec: {
            classMethods: [
              { name: 'async_create_session', api_mode: 'async' },
              { name: 'async_delete_session', api_mode: 'async' },
            ],
            containerSpec: { imageUri: RUNTIME_IMAGE },
          },
        })
      }
      if (!baseImplementation) throw new Error('missing base fetch implementation')
      return baseImplementation(input, init)
    })

    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'agent-runtime',
      ok: false,
      code: 'AGENT_RUNTIME_CLASS_METHOD_MISMATCH',
      detail: 'async_stream_query:async_stream',
    }))
  })

  it('fails closed when the release pin names a different Runtime resource', async () => {
    const result = await runGcpPreflight({
      ...options(),
      runtimePin: {
        resourceName: `projects/${PROJECT_ID}/locations/${RUNTIME_REGION}/reasoningEngines/999`,
        imageUri: RUNTIME_IMAGE,
      },
    })

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'agent-runtime',
      ok: false,
      code: 'AGENT_RUNTIME_RESOURCE_MISMATCH',
    }))
  })

  it('fails closed when the deployed Runtime image differs from the immutable release pin', async () => {
    const result = await runGcpPreflight({
      ...options(),
      runtimePin: {
        resourceName: RUNTIME_RESOURCE,
        imageUri: `${RUNTIME_REGION}-docker.pkg.dev/${PROJECT_ID}/caffemate-agents/caffemate-agent-runtime@sha256:${'b'.repeat(64)}`,
      },
    })

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'agent-runtime',
      ok: false,
      code: 'AGENT_RUNTIME_IMAGE_MISMATCH',
      detail: RUNTIME_IMAGE,
    }))
  })

  it('fails closed when the pinned reranker cannot run through the Seoul RAG endpoint', async () => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith(':retrieveContexts')) {
        const body = JSON.parse(String(init?.body)) as {
          query?: { ragRetrievalConfig?: { ranking?: unknown } }
        }
        if (body.query?.ragRetrievalConfig?.ranking) {
          return Response.json({ error: { message: 'ranker unavailable' } }, { status: 503 })
        }
      }
      if (!baseImplementation) throw new Error('missing base fetch implementation')
      return baseImplementation(input, init)
    })

    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'reranker',
      ok: false,
      code: 'RERANKER_PREFLIGHT_FAILED',
      detail: 'HTTP 503',
    }))
    expect(fetchImpl.mock.calls.some(([input]) => String(input).includes('discoveryengine.googleapis.com'))).toBe(false)
  })

  it('keeps the Agent path blocked and makes no generation request before a model is approved', async () => {
    const fetchImpl = successfulFetch()
    const withoutModel = { ...options(fetchImpl), approvedModelId: undefined }
    const result = await runGcpPreflight(withoutModel)

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'generation-model',
      ok: false,
      code: 'MODEL_NOT_APPROVED',
    }))
    expect(fetchImpl.mock.calls.some(([input]) => String(input).includes(':generateContent'))).toBe(false)
  })

  it('fails closed when the official corpus exists but contains no ACTIVE files', async () => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/ragFiles?')) return Response.json({})
      if (!baseImplementation) throw new Error('missing base fetch implementation')
      return baseImplementation(input, init)
    })

    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'rag-files',
      ok: false,
      code: 'RAG_CORPUS_EMPTY',
    }))
  })

  it('fails when the CaffeMate RAG corpus is absent instead of reusing an unrelated corpus', async () => {
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url.includes('/ragCorpora?')) {
        return Response.json({
          ragCorpora: [{
            name: `projects/${PROJECT_ID}/locations/${RAG_REGION}/ragCorpora/1`,
            displayName: 'crowd-route-official-grounding-v1',
          }],
        })
      }
      if (url.includes('/reasoningEngines?')) return Response.json({})
      return Response.json({})
    })

    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'rag-corpus',
      ok: false,
      code: 'RAG_CORPUS_NOT_FOUND',
    }))
  })

  it('rejects location drift instead of changing runtime, generation, or data locations', async () => {
    await expect(runGcpPreflight({
      ...options(),
      runtimeRegion: 'global' as typeof RUNTIME_REGION,
    })).rejects.toMatchObject({ code: 'GCP_RUNTIME_REGION_NOT_ALLOWED' })

    await expect(runGcpPreflight({
      ...options(),
      generationRegion: 'asia-northeast3' as typeof GENERATION_REGION,
    })).rejects.toMatchObject({ code: 'GCP_GENERATION_REGION_NOT_ALLOWED' })
  })
})
