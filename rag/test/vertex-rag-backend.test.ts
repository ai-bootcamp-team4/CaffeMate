import { describe, expect, it, vi } from 'vitest'
import { createVertexRagBackend, VertexRagError } from '../src/vertex-rag-backend'

const PROJECT_ID = 'proj-aj20-211200020328'
const REGION = 'asia-northeast3'

describe('Vertex RAG Engine backend', () => {
  it('calls retrieveContexts in Seoul and constrains project retrieval to mapped RAG file ids', async () => {
    const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      expect(String(url)).toBe(
        `https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${REGION}:retrieveContexts`,
      )
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer adc-token')
      expect(JSON.parse(String(init?.body))).toEqual({
        vertexRagStore: {
          ragResources: [{
            ragCorpus: `projects/${PROJECT_ID}/locations/${REGION}/ragCorpora/1234`,
            ragFileIds: ['rag-file-1'],
          }],
        },
        query: {
          text: '임대료',
          ragRetrievalConfig: {
            topK: 5,
            ranking: {
              rankService: { modelName: 'semantic-ranker-default-004' },
            },
            filter: { metadataFilter: 'document_type == "LEASE"' },
          },
        },
      })
      return Response.json({
        contexts: {
          contexts: [{
            sourceUri: 'gs://caffemate-projects/project-1/doc-1.pdf',
            sourceDisplayName: '임대차계약서',
            text: '월 임대료 300만원',
            chunk: { pageSpan: { firstPage: 1, lastPage: 1 } },
          }],
        },
      })
    })
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl,
      mapContext: (context) => ({
        documentRevisionId: 'docrev-1',
        title: context.sourceDisplayName,
        anchor: 'page:1',
        excerpt: context.text,
        sourceDate: '2026-08-21',
        evidenceId: 'ev-1',
      }),
    })

    const result = await backend({
      corpusKind: 'PROJECT',
      corpusId: '1234',
      ventureProjectId: 'project-1',
      query: '임대료',
      documentType: 'LEASE',
      documentRevisionIds: ['docrev-1'],
      ragFileIds: ['rag-file-1'],
      limit: 5,
    })

    expect(result).toEqual([{
      documentRevisionId: 'docrev-1',
      title: '임대차계약서',
      anchor: 'page:1',
      excerpt: '월 임대료 300만원',
      sourceDate: '2026-08-21',
      evidenceId: 'ev-1',
    }])
  })


  it('applies official source-family and as-of metadata filters server-side', async () => {
    const fetchImpl = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toMatchObject({
        query: {
          ragRetrievalConfig: {
            ranking: {
              rankService: { modelName: 'semantic-ranker-default-004' },
            },
            filter: {
              metadataFilter: '(source_family == "LAW" || source_family == "GOVERNMENT_GUIDE") && published_or_data_date <= "2026-08-21"',
            },
          },
        },
      })
      return Response.json({ contexts: { contexts: [] } })
    })
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl,
      mapContext: () => null,
    })

    await backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '가맹사업법',
      sourceFamilies: ['LAW', 'GOVERNMENT_GUIDE'],
      asOf: '2026-08-21',
      limit: 5,
    })

    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('rejects project retrieval when no server-side RAG file fence was resolved', async () => {
    const fetchImpl = vi.fn()
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl,
      mapContext: () => null,
    })

    await expect(backend({
      corpusKind: 'PROJECT',
      corpusId: '1234',
      ventureProjectId: 'project-1',
      query: '임대료',
      documentRevisionIds: ['docrev-1'],
      limit: 5,
    })).rejects.toMatchObject({ code: 'RAG_FILE_SCOPE_MISSING' })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('rejects a corpus resource from another project or region', async () => {
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      mapContext: () => null,
    })

    await expect(backend({
      corpusKind: 'OFFICIAL',
      corpusId: 'projects/other/locations/us-central1/ragCorpora/1234',
      query: '가맹사업법',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 5,
    })).rejects.toBeInstanceOf(VertexRagError)
  })

  it('fails closed when a returned context cannot be mapped to an authoritative revision and anchor', async () => {
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        contexts: { contexts: [{ sourceUri: 'gs://unknown.pdf', sourceDisplayName: 'unknown', text: 'text' }] },
      }),
      mapContext: () => null,
    })

    await expect(backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 5,
    })).rejects.toMatchObject({ code: 'RAG_CONTEXT_MAPPING_MISSING' })
  })

  it.each([
    ['missing contexts envelope', { unexpected: 'schema-drift' }],
    ['non-array context rows', { contexts: { contexts: { sourceUri: 'gs://source' } } }],
    ['malformed context row', { contexts: { contexts: [{ sourceUri: 'gs://source', text: 'missing display name' }] } }],
  ])('fails closed for malformed 2xx response: %s', async (_label, payload) => {
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json(payload),
      mapContext: () => null,
    })

    await expect(backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 5,
    })).rejects.toMatchObject({ code: 'RAG_PROVIDER_PROTOCOL_ERROR' })
  })

  it('fails closed when Vertex returns more contexts than requested', async () => {
    const row = {
      sourceUri: 'gs://source',
      sourceDisplayName: 'source',
      text: 'text',
    }
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({ contexts: { contexts: [row, row] } }),
      mapContext: () => ({
        documentRevisionId: 'docrev-1',
        title: 'source',
        anchor: 'anchor',
        excerpt: 'text',
        sourceDate: '2026-08-21',
        evidenceId: 'ev-1',
      }),
    })

    await expect(backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 1,
    })).rejects.toMatchObject({ code: 'RAG_RESULT_LIMIT_EXCEEDED' })
  })

  it('aborts a hung Vertex fetch at the connector deadline', async () => {
    const fetchImpl = vi.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
    }))
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: fetchImpl as typeof fetch,
      mapContext: () => null,
      timeoutMs: 10,
    })

    await expect(backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 1,
    })).rejects.toMatchObject({ code: 'RAG_TIMEOUT' })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('applies the retrieval deadline while access-token acquisition is stalled', async () => {
    const accessToken = vi.fn(() => new Promise<string>(() => undefined))
    const fetchImpl = vi.fn()
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken,
      fetchImpl,
      mapContext: () => null,
      timeoutMs: 10,
    })

    const outcome = backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 1,
    }).then(
      () => ({ code: 'RESOLVED' }),
      (error: unknown) => error,
    )

    const result = await Promise.race([
      outcome,
      new Promise((resolve) => setTimeout(() => resolve({ code: 'STILL_PENDING' }), 50)),
    ])
    expect(result).toMatchObject({ code: 'RAG_TIMEOUT' })
    expect(accessToken).toHaveBeenCalledOnce()
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('propagates caller cancellation while access-token acquisition is stalled', async () => {
    const controller = new AbortController()
    const accessToken = vi.fn(() => new Promise<string>(() => undefined))
    const fetchImpl = vi.fn()
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken,
      fetchImpl,
      mapContext: () => null,
    })

    const outcome = backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 1,
      signal: controller.signal,
    }).then(
      () => ({ code: 'RESOLVED' }),
      (error: unknown) => error,
    )
    controller.abort(new Error('caller cancelled'))

    const result = await Promise.race([
      outcome,
      new Promise((resolve) => setTimeout(() => resolve({ code: 'STILL_PENDING' }), 50)),
    ])
    expect(result).toMatchObject({ code: 'RAG_CANCELLED' })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('propagates caller cancellation into the Vertex fetch', async () => {
    const controller = new AbortController()
    const fetchImpl = vi.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
      controller.abort(new Error('caller cancelled'))
    }))
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: fetchImpl as typeof fetch,
      mapContext: () => null,
    })

    await expect(backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 1,
      signal: controller.signal,
    })).rejects.toMatchObject({ code: 'RAG_CANCELLED' })
  })

  it('rejects a pre-cancelled request before fetching an access token or calling Vertex', async () => {
    const controller = new AbortController()
    controller.abort(new Error('caller cancelled'))
    const accessToken = vi.fn(async () => 'adc-token')
    const fetchImpl = vi.fn()
    const backend = createVertexRagBackend({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken,
      fetchImpl,
      mapContext: () => null,
    })

    await expect(backend({
      corpusKind: 'OFFICIAL',
      corpusId: '1234',
      query: '법령',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 1,
      signal: controller.signal,
    })).rejects.toMatchObject({ code: 'RAG_CANCELLED' })
    expect(accessToken).not.toHaveBeenCalled()
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})