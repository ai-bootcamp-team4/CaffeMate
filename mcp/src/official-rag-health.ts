import { OFFICIAL_RAG_SOURCE } from './official-rag'
import type { McpConfiguredSource } from './source-health'

interface OfficialRagHealthOptions {
  officialCorpusResource: string
  accessToken: () => Promise<string>
  fetch: typeof globalThis.fetch
}

interface VertexRagFile {
  name?: string
  fileStatus?: { state?: string }
  gcsSource?: { uris?: string[] }
}

export function createOfficialRagHealthSource(options: OfficialRagHealthOptions): McpConfiguredSource {
  const match = /^projects\/[^/]+\/locations\/([^/]+)\/ragCorpora\/[^/]+$/.exec(options.officialCorpusResource)
  if (!match) throw new Error('MCP_RAG_CORPUS_CONFIGURATION_INVALID')
  const region = match[1]
  const ragFileResource = `${options.officialCorpusResource}/ragFiles/${OFFICIAL_RAG_SOURCE.ragFileId}`
  const endpoint = `https://${region}-aiplatform.googleapis.com/v1beta1/${ragFileResource}`

  return {
    sourceId: OFFICIAL_RAG_SOURCE.sourceId,
    dataDate: OFFICIAL_RAG_SOURCE.sourceDate,
    probeHealth: async ({ observedAt }) => {
      const token = await options.accessToken()
      if (!token) {
        return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: OFFICIAL_RAG_SOURCE.sourceDate }
      }
      const response = await options.fetch(endpoint, {
        method: 'GET',
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
        signal: AbortSignal.timeout(5000),
      })
      if (!response.ok) {
        return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: OFFICIAL_RAG_SOURCE.sourceDate }
      }
      const ragFile = await response.json() as VertexRagFile
      const exactSource = ragFile.gcsSource?.uris?.length === 1
        && ragFile.gcsSource.uris[0] === OFFICIAL_RAG_SOURCE.sourceUri
      const healthy = ragFile.name === ragFileResource
        && ragFile.fileStatus?.state === 'ACTIVE'
        && exactSource
      if (!healthy) {
        return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: OFFICIAL_RAG_SOURCE.sourceDate }
      }
      return {
        status: 'HEALTHY',
        lastSuccessAt: observedAt.toISOString(),
        dataDate: OFFICIAL_RAG_SOURCE.sourceDate,
        sourceTrace: {
          sourceRef: OFFICIAL_RAG_SOURCE.sourceRef,
          dataDate: OFFICIAL_RAG_SOURCE.sourceDate,
          contentDigest: OFFICIAL_RAG_SOURCE.contentDigest,
        },
      }
    },
  }
}
