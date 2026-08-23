import { describe, expect, it, vi } from 'vitest'
import { createProductionMcpConnectors } from '../src/production-connectors'
import { OFFICIAL_RAG_SOURCE } from '../src/official-rag'
import { McpToolRouter } from '../src/router'

const projectId = 'proj-aj20-211200020328'
const region = 'asia-northeast3'
const projectNumber = '424808310695'
const officialCorpus = `projects/${projectId}/locations/${region}/ragCorpora/5148740273991319552`
const scope = { ventureProjectId: 'project-1', workflowRunId: 'workflow-1', requestId: 'request-1' }

describe('production MCP connector composition', () => {
  it('adds verified structured grounding and official RAG connectors', async () => {
    const accessToken = vi.fn(async () => 'access-token')
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      expect(init?.headers).toMatchObject({ Authorization: 'Bearer access-token' })
      if (url.endsWith(':retrieveContexts')) {
        expect(url).toBe(
          `https://${region}-aiplatform.googleapis.com/v1beta1/projects/${projectId}/locations/${region}:retrieveContexts`,
        )
        expect(JSON.parse(String(init?.body))).toEqual({
          vertexRagStore: { ragResources: [{ ragCorpus: officialCorpus }] },
          query: {
            text: '커피전문점 영업신고',
            ragRetrievalConfig: {
              topK: 3,
              filter: {
                metadataFilter: 'source_family == "GOVERNMENT_GUIDE" && published_or_data_date <= "2026-07-15"',
              },
            },
          },
        })
        return Response.json({
          contexts: {
            contexts: [{
              sourceUri: OFFICIAL_RAG_SOURCE.sourceUri,
              sourceDisplayName: 'source.html',
              text: '커피전문점은 휴게음식점 영업신고를 해야 합니다.',
              chunk: { fileId: OFFICIAL_RAG_SOURCE.ragFileId, chunkId: '5769839172020912571' },
              score: 0.15,
            }],
          },
        })
      }
      expect(url).toBe(
        `https://discoveryengine.googleapis.com/v1/projects/${projectId}/locations/${region}/rankingConfigs/default_ranking_config:rank`,
      )
      expect(init?.headers).toMatchObject({ 'X-Goog-User-Project': projectId })
      expect(JSON.parse(String(init?.body))).toEqual({
        model: 'semantic-ranker-default-004',
        query: '커피전문점 영업신고',
        records: [{
          id: 'context-0',
          title: 'source.html',
          content: '커피전문점은 휴게음식점 영업신고를 해야 합니다.',
        }],
        topN: 1,
      })
      return Response.json({
        records: [{
          id: 'context-0',
          title: 'source.html',
          content: '커피전문점은 휴게음식점 영업신고를 해야 합니다.',
          score: 0.91,
        }],
      })
    })

    const connectors = createProductionMcpConnectors({
      projectId,
      officialCorpusResource: officialCorpus,
      accessToken,
      fetch: fetcher as typeof fetch,
      now: () => new Date('2026-08-22T01:30:00Z'),
    })
    expect(Object.keys(connectors).sort()).toEqual([
      'get_area_profile',
      'get_source_health',
      'resolve_area',
      'retrieve_official_documents',
      'search_cafe_observations',
    ])

    const result = await new McpToolRouter(connectors).call('retrieve_official_documents', {
      query: '커피전문점 영업신고',
      source_families: ['GOVERNMENT_GUIDE'],
      as_of: '2026-07-15',
      limit: 3,
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'OK',
      project_id: 'project-1',
      request_id: 'request-1',
      tool_name: 'retrieve_official_documents',
      data: [{
        document_revision_id: 'easylaw-csmSeq-706@2026-07-15',
        evidence_id: `rag:${OFFICIAL_RAG_SOURCE.ragFileId}:5769839172020912571`,
      }],
      source_trace: [{
        source_id: 'easylaw-csmSeq-706',
        source_ref: OFFICIAL_RAG_SOURCE.sourceRef,
        data_date: '2026-07-15',
        retrieved_at: '2026-08-22T01:30:00.000Z',
        content_digest: 'sha256:f44af895c9dd771ba22d3890016928ba8bfaa3ed2306d9cd0a5b5bb6ee9d9c34',
      }],
    })
    expect(accessToken).toHaveBeenCalledOnce()
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('reports configured official RAG source health from the pinned active RagFile and keeps unknown sources unavailable', async () => {
    const accessToken = vi.fn(async () => 'access-token')
    const ragFileResource = `${officialCorpus}/ragFiles/${OFFICIAL_RAG_SOURCE.ragFileId}`
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(String(input)).toBe(`https://${region}-aiplatform.googleapis.com/v1beta1/${ragFileResource}`)
      expect(init?.method).toBe('GET')
      expect(init?.headers).toMatchObject({ Authorization: 'Bearer access-token' })
      return Response.json({
        name: ragFileResource.replace(`projects/${projectId}/`, `projects/${projectNumber}/`),
        fileStatus: { state: 'ACTIVE' },
        gcsSource: { uris: [OFFICIAL_RAG_SOURCE.sourceUri] },
      })
    })
    const connectors = createProductionMcpConnectors({
      projectId,
      officialCorpusResource: officialCorpus,
      accessToken,
      fetch: fetcher as typeof fetch,
      now: () => new Date('2026-08-22T01:30:00Z'),
    })

    const result = await new McpToolRouter(connectors).call('get_source_health', {
      source_ids: [OFFICIAL_RAG_SOURCE.sourceId, 'unknown-source'],
      as_of: '2026-08-22',
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'PARTIAL',
      data: [
        {
          source_id: OFFICIAL_RAG_SOURCE.sourceId,
          status: 'HEALTHY',
          last_success_at: '2026-08-22T01:30:00.000Z',
          data_date: OFFICIAL_RAG_SOURCE.sourceDate,
        },
        { source_id: 'unknown-source', status: 'UNAVAILABLE', last_success_at: null, data_date: null },
      ],
      source_trace: [{
        source_id: OFFICIAL_RAG_SOURCE.sourceId,
        source_ref: OFFICIAL_RAG_SOURCE.sourceRef,
        data_date: OFFICIAL_RAG_SOURCE.sourceDate,
        retrieved_at: '2026-08-22T01:30:00.000Z',
        content_digest: OFFICIAL_RAG_SOURCE.contentDigest,
      }],
    })
    expect(accessToken).toHaveBeenCalledOnce()
    expect(fetcher).toHaveBeenCalledOnce()
  })

  it('bounds official source health even when ADC token acquisition stalls', async () => {
    const accessToken = vi.fn(() => new Promise<string>(() => undefined))
    const connectors = createProductionMcpConnectors({
      projectId,
      officialCorpusResource: officialCorpus,
      accessToken,
      fetch: vi.fn() as typeof fetch,
      now: () => new Date('2026-08-22T01:30:00Z'),
      officialRagHealthTimeoutMs: 10,
    })

    const outcome = new McpToolRouter(connectors).call('get_source_health', {
      source_ids: [OFFICIAL_RAG_SOURCE.sourceId],
      as_of: '2026-08-22',
    }, scope)

    const result = await Promise.race([
      outcome,
      new Promise((resolve) => setTimeout(() => resolve('STILL_PENDING'), 50)),
    ])
    expect(result).toMatchObject({
      status: 'PARTIAL',
      data: [{ source_id: OFFICIAL_RAG_SOURCE.sourceId, status: 'UNAVAILABLE' }],
    })
  })

  it('preserves the configured official source data date when the health probe fails', async () => {
    const connectors = createProductionMcpConnectors({
      projectId,
      officialCorpusResource: officialCorpus,
      accessToken: async () => { throw new Error('adc unavailable') },
      fetch: vi.fn() as typeof fetch,
      now: () => new Date('2026-08-22T01:30:00Z'),
    })

    const result = await new McpToolRouter(connectors).call('get_source_health', {
      source_ids: [OFFICIAL_RAG_SOURCE.sourceId],
      as_of: '2026-08-22',
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'PARTIAL',
      data: [{
        source_id: OFFICIAL_RAG_SOURCE.sourceId,
        status: 'UNAVAILABLE',
        last_success_at: null,
        data_date: OFFICIAL_RAG_SOURCE.sourceDate,
      }],
    })
  })

  it.each([
    ['RagFile error state', { fileStatus: { state: 'ERROR' }, gcsSource: { uris: [OFFICIAL_RAG_SOURCE.sourceUri] } }],
    ['unexpected GCS source', { fileStatus: { state: 'ACTIVE' }, gcsSource: { uris: ['gs://other/source.html'] } }],
  ])('does not report official RAG healthy for %s', async (_label, partialRagFile) => {
    const ragFileResource = `${officialCorpus}/ragFiles/${OFFICIAL_RAG_SOURCE.ragFileId}`
    const connectors = createProductionMcpConnectors({
      projectId,
      officialCorpusResource: officialCorpus,
      accessToken: async () => 'access-token',
      fetch: vi.fn(async () => Response.json({ name: ragFileResource, ...partialRagFile })) as typeof fetch,
      now: () => new Date('2026-08-22T01:30:00Z'),
    })

    const result = await new McpToolRouter(connectors).call('get_source_health', {
      source_ids: [OFFICIAL_RAG_SOURCE.sourceId],
      as_of: '2026-08-22',
    }, scope) as Record<string, unknown>

    expect(result).toMatchObject({
      status: 'PARTIAL',
      data: [{
        source_id: OFFICIAL_RAG_SOURCE.sourceId,
        status: 'UNAVAILABLE',
        last_success_at: null,
        data_date: OFFICIAL_RAG_SOURCE.sourceDate,
      }],
      source_trace: [],
    })
  })

  it('requires an exact Seoul official corpus resource at startup', () => {
    expect(() => createProductionMcpConnectors({
      projectId,
      officialCorpusResource: 'projects/other/locations/us-central1/ragCorpora/1',
      accessToken: async () => 'token',
    })).toThrow(/MCP_RAG_CORPUS_CONFIGURATION_INVALID/)
  })
})
