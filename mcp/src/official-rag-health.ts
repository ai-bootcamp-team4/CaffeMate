import { OFFICIAL_RAG_SOURCE } from './official-rag'
import type { McpConfiguredSource } from './source-health'

interface OfficialRagHealthOptions {
  officialCorpusResource: string
  accessToken: () => Promise<string>
  fetch: typeof globalThis.fetch
  timeoutMs?: number
}

interface VertexRagFile {
  name?: string
  fileStatus?: { state?: string }
  gcsSource?: { uris?: string[] }
}

interface RagFileIdentity {
  location: string
  corpusId: string
  fileId: string
}

const DEFAULT_HEALTH_TIMEOUT_MS = 5000

function ragFileIdentity(resourceName: string): RagFileIdentity | null {
  const match = /^projects\/[^/]+\/locations\/([^/]+)\/ragCorpora\/([^/]+)\/ragFiles\/([^/]+)$/.exec(resourceName)
  if (!match) return null
  const [, location, corpusId, fileId] = match
  return location && corpusId && fileId ? { location, corpusId, fileId } : null
}

function sameRagFile(actual: string | undefined, expected: string): boolean {
  if (!actual) return false
  const left = ragFileIdentity(actual)
  const right = ragFileIdentity(expected)
  return Boolean(left && right
    && left.location === right.location
    && left.corpusId === right.corpusId
    && left.fileId === right.fileId)
}

async function awaitWithSignal<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) throw signal.reason ?? new Error('operation aborted')
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(signal.reason ?? new Error('operation aborted'))
    signal.addEventListener('abort', onAbort, { once: true })
    operation.then(
      (value) => { signal.removeEventListener('abort', onAbort); resolve(value) },
      (error: unknown) => { signal.removeEventListener('abort', onAbort); reject(error) },
    )
  })
}

export function createOfficialRagHealthSource(options: OfficialRagHealthOptions): McpConfiguredSource {
  const match = /^projects\/[^/]+\/locations\/([^/]+)\/ragCorpora\/[^/]+$/.exec(options.officialCorpusResource)
  if (!match) throw new Error('MCP_RAG_CORPUS_CONFIGURATION_INVALID')
  const region = match[1]
  const ragFileResource = `${options.officialCorpusResource}/ragFiles/${OFFICIAL_RAG_SOURCE.ragFileId}`
  const endpoint = `https://${region}-aiplatform.googleapis.com/v1beta1/${ragFileResource}`
  const timeoutMs = options.timeoutMs ?? DEFAULT_HEALTH_TIMEOUT_MS
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('MCP_RAG_HEALTH_TIMEOUT_INVALID')

  return {
    sourceId: OFFICIAL_RAG_SOURCE.sourceId,
    dataDate: OFFICIAL_RAG_SOURCE.sourceDate,
    probeHealth: async ({ observedAt }) => {
      const signal = AbortSignal.timeout(timeoutMs)
      try {
        const token = await awaitWithSignal(options.accessToken(), signal)
        if (!token) {
          return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: OFFICIAL_RAG_SOURCE.sourceDate }
        }
        const response = await options.fetch(endpoint, {
          method: 'GET',
          headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
          signal,
        })
        if (!response.ok) {
          return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: OFFICIAL_RAG_SOURCE.sourceDate }
        }
        const ragFile = await response.json() as VertexRagFile
        const exactSource = ragFile.gcsSource?.uris?.length === 1
          && ragFile.gcsSource.uris[0] === OFFICIAL_RAG_SOURCE.sourceUri
        const healthy = sameRagFile(ragFile.name, ragFileResource)
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
      } catch {
        return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate: OFFICIAL_RAG_SOURCE.sourceDate }
      }
    },
  }
}
