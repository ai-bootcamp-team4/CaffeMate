import franchiseRegistry from '../data/franchise-rag-file-registry-20260825.json'
import type { VertexRagContext } from '../../rag/src/vertex-rag-backend'
import type { RagBackendRequest, RagHit } from '../../rag/src/retrieval'

export interface OfficialRagSource {
  sourceId: string
  brandId?: string
  sourceFamily: string
  claimType?: string
  documentRevisionId: string
  title: string
  sourceDate: string
  sourceRef: string
  sourceUri: string
  gcsGeneration?: string
  ragFileId: string | null
  contentDigest: string
}

export const OFFICIAL_RAG_SOURCE: OfficialRagSource = Object.freeze({
  sourceId: 'easylaw-csmSeq-706',
  sourceFamily: 'GOVERNMENT_GUIDE',
  claimType: 'CAFE_OPENING_REQUIRED_PROCEDURES',
  documentRevisionId: 'easylaw-csmSeq-706@2026-07-15',
  title: '커피전문점 영업신고 및 사업자등록',
  sourceDate: '2026-07-15',
  sourceRef: 'http://easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=706&ccfNo=3&cciNo=2&cnpClsNo=1',
  sourceUri: 'gs://proj-aj20-211200020328-caffemate-grounding/official/easylaw/coffee-business-registration/2026-08-22/source.html',
  gcsGeneration: '1787329995006379',
  ragFileId: '5769839172015160639',
  contentDigest: 'sha256:f44af895c9dd771ba22d3890016928ba8bfaa3ed2306d9cd0a5b5bb6ee9d9c34',
})

export const PREPARED_FRANCHISE_RAG_SOURCES: readonly OfficialRagSource[] =
  franchiseRegistry.sources

export const OFFICIAL_RAG_SOURCES: readonly OfficialRagSource[] = [
  OFFICIAL_RAG_SOURCE,
  ...PREPARED_FRANCHISE_RAG_SOURCES,
]

export function pinnedRagFileIdsBySourceFamily(
  sources: readonly OfficialRagSource[],
): Readonly<Record<string, readonly string[]>> {
  const grouped = new Map<string, OfficialRagSource[]>()
  for (const source of sources) {
    const family = grouped.get(source.sourceFamily) ?? []
    family.push(source)
    grouped.set(source.sourceFamily, family)
  }
  return Object.fromEntries(
    [...grouped]
      .filter(([, family]) => family.every((source) => source.ragFileId !== null))
      .map(([family, sourcesInFamily]) => [
        family,
        [...new Set(sourcesInFamily.map((source) => source.ragFileId as string))],
      ]),
  )
}

function chunkIdentity(chunk: unknown): { fileId: string; chunkId: string } | null {
  if (!chunk || typeof chunk !== 'object' || Array.isArray(chunk)) return null
  const value = chunk as Record<string, unknown>
  if (typeof value.fileId !== 'string' || typeof value.chunkId !== 'string') return null
  if (!value.fileId || !value.chunkId) return null
  return { fileId: value.fileId, chunkId: value.chunkId }
}

export function createOfficialRagContextMapper(sources: readonly OfficialRagSource[]) {
  return (context: VertexRagContext, request: RagBackendRequest): RagHit | null => {
    if (request.corpusKind !== 'OFFICIAL') return null
    const chunk = chunkIdentity(context.chunk)
    if (!chunk) return null
    const source = sources.find((candidate) => (
      candidate.ragFileId !== null
      && context.sourceUri === candidate.sourceUri
      && chunk.fileId === candidate.ragFileId
    ))
    if (!source) return null
    if (!request.sourceFamilies?.includes(source.sourceFamily)) return null
    if (!request.asOf || source.sourceDate > request.asOf) return null

    return {
      documentRevisionId: source.documentRevisionId,
      title: source.title,
      anchor: `${source.sourceRef}#rag-file=${chunk.fileId}&chunk=${chunk.chunkId}`,
      excerpt: context.text,
      sourceDate: source.sourceDate,
      evidenceId: `rag:${chunk.fileId}:${chunk.chunkId}`,
      sourceFamily: source.sourceFamily,
      ...(source.claimType ? { claimType: source.claimType } : {}),
      ...(source.brandId ? { brandId: source.brandId } : {}),
      sourceId: source.sourceId,
      source: {
        sourceId: source.sourceId,
        sourceRef: source.sourceRef,
        dataDate: source.sourceDate,
        contentDigest: source.contentDigest,
      },
    }
  }
}

export const mapOfficialRagContext = createOfficialRagContextMapper(OFFICIAL_RAG_SOURCES)
