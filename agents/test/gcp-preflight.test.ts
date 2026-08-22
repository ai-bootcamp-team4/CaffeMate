import { describe, expect, it, vi } from 'vitest'
import { runGcpPreflight } from '../src/gcp-preflight'

const PROJECT_ID = 'proj-aj20-211200020328'
const RUNTIME_REGION = 'asia-northeast3'
const GENERATION_REGION = 'global'
const RAG_REGION = 'asia-northeast3'
const EMBEDDING_REGION = 'asia-northeast3'
const MODEL_ID = 'gemini-3.7-flash'
const RAG_CORPUS_RESOURCE = `projects/${PROJECT_ID}/locations/${RAG_REGION}/ragCorpora/5148740273991319552`
const RAG_FILE_RESOURCE = `${RAG_CORPUS_RESOURCE}/ragFiles/5769839172015160639`
const SECOND_RAG_FILE_RESOURCE = `${RAG_CORPUS_RESOURCE}/ragFiles/5769839172015160640`
const RUNTIME_PROJECT_NUMBER = '424808310695'
const RUNTIME_RESOURCE = `projects/${PROJECT_ID}/locations/${RUNTIME_REGION}/reasoningEngines/777`
const RUNTIME_CANONICAL_RESOURCE = `projects/${RUNTIME_PROJECT_NUMBER}/locations/${RUNTIME_REGION}/reasoningEngines/777`
const RUNTIME_IMAGE = `${RUNTIME_REGION}-docker.pkg.dev/${PROJECT_ID}/caffemate-agents/caffemate-agent-runtime@sha256:${'a'.repeat(64)}`
const PROMPT_BUNDLE_DIGEST = `sha256:${'b'.repeat(64)}`
const AGENT_CONTRACT_BUNDLE_DIGEST = `sha256:${'c'.repeat(64)}`

function successfulFetch() {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/ragCorpora/5148740273991319552')) {
      return Response.json({
        name: RAG_CORPUS_RESOURCE,
        displayName: 'caffemate-official-v1',
        corpusStatus: { state: 'ACTIVE' },
        vectorDbConfig: {
          ragEmbeddingModelConfig: {
            vertexPredictionEndpoint: {
              endpoint: `projects/${PROJECT_ID}/locations/${EMBEDDING_REGION}/publishers/google/models/text-multilingual-embedding-002`,
            },
          },
        },
      })
    }
    if (url.includes('/ragFiles?')) {
      return Response.json({
        ragFiles: [{
          name: RAG_FILE_RESOURCE,
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
    if (url.endsWith('/reasoningEngines/777')) {
      return Response.json({
        name: RUNTIME_CANONICAL_RESOURCE,
        displayName: 'caffemate-agents',
        spec: {
          classMethods: [
            { name: 'async_create_session', api_mode: 'async' },
            { name: 'async_stream_query', api_mode: 'async_stream' },
            { name: 'async_delete_session', api_mode: 'async' },
            { name: 'async_get_release_identity', api_mode: 'async' },
          ],
          containerSpec: { imageUri: RUNTIME_IMAGE },
        },
      })
    }
    if (url.endsWith('/reasoningEngines/777:query')) {
      expect(JSON.parse(String(init?.body))).toEqual({
        class_method: 'async_get_release_identity',
        input: {},
      })
      return Response.json({
        output: {
          schema_version: '1.0.0',
          prompt_bundle_digest: PROMPT_BUNDLE_DIGEST,
          agent_contract_bundle_digest: AGENT_CONTRACT_BUNDLE_DIGEST,
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
      promptBundleDigest: PROMPT_BUNDLE_DIGEST,
      agentContractBundleDigest: AGENT_CONTRACT_BUNDLE_DIGEST,
    },
    ragPin: {
      corpusResourceName: RAG_CORPUS_RESOURCE,
      ragFileResourceNames: [RAG_FILE_RESOURCE],
      embeddingModelId: 'text-multilingual-embedding-002',
      rerankerId: 'semantic-ranker-default-004',
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
    expect(result.ragCorpusResource).toBe(RAG_CORPUS_RESOURCE)
    expect(result.ragFileResources).toEqual([RAG_FILE_RESOURCE])
    expect(result.embeddingModelId).toBe('text-multilingual-embedding-002')
    expect(result.rerankerId).toBe('semantic-ranker-default-004')
    expect(result.generationModelId).toBe(MODEL_ID)
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
    expect(urls.some((url) => url.includes('/reasoningEngines?'))).toBe(false)
    expect(urls).toContain(
      `https://${RUNTIME_REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${RUNTIME_REGION}/reasoningEngines/777`,
    )
    expect(urls).toContain(`https://${RAG_REGION}-aiplatform.googleapis.com/v1/${RAG_CORPUS_RESOURCE}`)
    expect(urls.filter((url) => url.includes('/ragCorpora/') || url.endsWith(':retrieveContexts')).every((url) => url.includes(RAG_REGION))).toBe(true)
    expect(urls.some((url) => url.includes('/ragCorpora?'))).toBe(false)
    expect(urls.some((url) => url.includes('discoveryengine.googleapis.com'))).toBe(false)
  })

  it('consumes every RAG file list page before comparing the ACTIVE IndexGeneration file set', async () => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/ragFiles?')) {
        const pageToken = new URL(url).searchParams.get('pageToken')
        if (!pageToken) {
          return Response.json({
            ragFiles: [{ name: RAG_FILE_RESOURCE, fileStatus: { state: 'ACTIVE' } }],
            nextPageToken: 'page-2',
          })
        }
        expect(pageToken).toBe('page-2')
        return Response.json({
          ragFiles: [{ name: SECOND_RAG_FILE_RESOURCE, fileStatus: { state: 'ACTIVE' } }],
        })
      }
      if (!baseImplementation) throw new Error('missing base fetch implementation')
      return baseImplementation(input, init)
    })

    const result = await runGcpPreflight({
      ...options(fetchImpl),
      ragPin: {
        ...options(fetchImpl).ragPin,
        ragFileResourceNames: [RAG_FILE_RESOURCE, SECOND_RAG_FILE_RESOURCE],
      },
    })

    expect(result.ok).toBe(true)
    expect(result.ragFileResources).toEqual([RAG_FILE_RESOURCE, SECOND_RAG_FILE_RESOURCE])
    expect(fetchImpl.mock.calls.filter(([input]) => String(input).includes('/ragFiles?'))).toHaveLength(2)
  })

  it('exercises the production HIGH structured-output generation contract', async () => {
    const fetchImpl = successfulFetch()

    await runGcpPreflight(options(fetchImpl))

    const generationCall = fetchImpl.mock.calls.find(([input]) => String(input).includes(':generateContent'))
    expect(generationCall).toBeDefined()
    const body = JSON.parse(String(generationCall?.[1]?.body)) as Record<string, unknown>
    expect(body).toMatchObject({
      systemInstruction: { parts: [{ text: expect.any(String) }] },
      contents: [{ role: 'user', parts: [{ text: expect.any(String) }] }],
      generationConfig: {
        candidateCount: 1,
        responseMimeType: 'application/json',
        responseJsonSchema: {
          type: 'object',
          additionalProperties: false,
          required: ['ok'],
          properties: {
            ok: { type: 'boolean' },
          },
        },
        seed: 17,
        thinkingConfig: { thinkingLevel: 'HIGH' },
        maxOutputTokens: 8192,
      },
    })
  })

  it.each([
    {
      name: 'multiple candidates',
      payload: {
        candidates: [
          { content: { parts: [{ text: '{"ok":true}' }] }, finishReason: 'STOP' },
          { content: { parts: [{ text: '{"ok":true}' }] }, finishReason: 'STOP' },
        ],
      },
    },
    {
      name: 'extra response parts',
      payload: {
        candidates: [{
          content: { parts: [{ text: '{"ok":true}' }, { text: 'extra' }] },
          finishReason: 'STOP',
        }],
      },
    },
    {
      name: 'mixed function payload',
      payload: {
        candidates: [{
          content: { parts: [{ text: '{"ok":true}', functionCall: { name: 'unexpected' } }] },
          finishReason: 'STOP',
        }],
      },
    },
  ])('fails generation preflight on $name rejected by the production client', async ({ payload }) => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).includes(`${MODEL_ID}:generateContent`)) return Response.json(payload)
      if (!baseImplementation) throw new Error('missing base fetch implementation')
      return baseImplementation(input, init)
    })

    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'generation-model',
      ok: false,
      code: 'GENERATION_RESPONSE_INVALID',
    }))
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

  it('fails closed when the exact release-pinned Runtime resource cannot be read', async () => {
    const result = await runGcpPreflight({
      ...options(),
      runtimePin: {
        resourceName: `projects/${PROJECT_ID}/locations/${RUNTIME_REGION}/reasoningEngines/999`,
        imageUri: RUNTIME_IMAGE,
        promptBundleDigest: PROMPT_BUNDLE_DIGEST,
        agentContractBundleDigest: AGENT_CONTRACT_BUNDLE_DIGEST,
      },
    })

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'agent-runtime',
      ok: false,
      code: 'AGENT_RUNTIME_GET_FAILED',
      detail: 'HTTP 404',
    }))
  })

  it('fails closed when the deployed Runtime image differs from the immutable release pin', async () => {
    const result = await runGcpPreflight({
      ...options(),
      runtimePin: {
        resourceName: RUNTIME_RESOURCE,
        imageUri: `${RUNTIME_REGION}-docker.pkg.dev/${PROJECT_ID}/caffemate-agents/caffemate-agent-runtime@sha256:${'b'.repeat(64)}`,
        promptBundleDigest: PROMPT_BUNDLE_DIGEST,
        agentContractBundleDigest: AGENT_CONTRACT_BUNDLE_DIGEST,
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

  it('fails closed when the deployed Runtime prompt or payload-schema contents differ from the release pin', async () => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).endsWith('/reasoningEngines/777:query')) {
        return Response.json({
          output: {
            schema_version: '1.0.0',
            prompt_bundle_digest: `sha256:${'d'.repeat(64)}`,
            agent_contract_bundle_digest: AGENT_CONTRACT_BUNDLE_DIGEST,
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
      code: 'AGENT_RUNTIME_RELEASE_IDENTITY_MISMATCH',
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

  it('fails when the exact release-pinned RAG corpus cannot be read', async () => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).endsWith('/ragCorpora/5148740273991319552')) {
        return Response.json({ error: { message: 'not found' } }, { status: 404 })
      }
      if (!baseImplementation) throw new Error('missing base fetch implementation')
      return baseImplementation(input, init)
    })

    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'rag-corpus',
      ok: false,
      code: 'RAG_CORPUS_GET_FAILED',
      detail: 'HTTP 404',
    }))
  })

  it('fails when ACTIVE RAG files differ from the release-pinned IndexGeneration source set', async () => {
    const fetchImpl = successfulFetch()
    const baseImplementation = fetchImpl.getMockImplementation()
    fetchImpl.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).includes('/ragFiles?')) {
        return Response.json({
          ragFiles: [
            { name: RAG_FILE_RESOURCE, fileStatus: { state: 'ACTIVE' } },
            { name: `${RAG_CORPUS_RESOURCE}/ragFiles/unsealed-extra`, fileStatus: { state: 'ACTIVE' } },
          ],
        })
      }
      if (!baseImplementation) throw new Error('missing base fetch implementation')
      return baseImplementation(input, init)
    })

    const result = await runGcpPreflight(options(fetchImpl))

    expect(result.ok).toBe(false)
    expect(result.checks).toContainEqual(expect.objectContaining({
      name: 'rag-files',
      ok: false,
      code: 'RAG_FILE_SET_MISMATCH',
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
