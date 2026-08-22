import type { VertexRagContext } from '../../rag/src/vertex-rag-backend'
import type { RagBackendRequest, RagHit } from '../../rag/src/retrieval'

export const OFFICIAL_RAG_SOURCE = Object.freeze({
  sourceId: 'easylaw-csmSeq-706',
  documentRevisionId: 'easylaw-csmSeq-706@2026-07-15',
  title: '커피전문점 영업신고 및 사업자등록',
  sourceDate: '2026-07-15',
  sourceRef: 'http://easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=706&ccfNo=3&cciNo=2&cnpClsNo=1',
  sourceUri: 'gs://proj-aj20-211200020328-caffemate-grounding/official/easylaw/coffee-business-registration/2026-08-22/source.html',
  ragFileId: '5769839172015160639',
  contentDigest: 'sha256:f44af895c9dd771ba22d3890016928ba8bfaa3ed2306d9cd0a5b5bb6ee9d9c34',
} as const)

function chunkIdentity(chunk: unknown): { fileId: string; chunkId: string } | null {
  if (!chunk || typeof chunk !== 'object' || Array.isArray(chunk)) return null
  const value = chunk as Record<string, unknown>
  if (typeof value.fileId !== 'string' || typeof value.chunkId !== 'string') return null
  if (!value.fileId || !value.chunkId) return null
  return { fileId: value.fileId, chunkId: value.chunkId }
}

export function mapOfficialRagContext(context: VertexRagContext, request: RagBackendRequest): RagHit | null {
  if (request.corpusKind !== 'OFFICIAL' || context.sourceUri !== OFFICIAL_RAG_SOURCE.sourceUri) return null
  const chunk = chunkIdentity(context.chunk)
  if (!chunk || chunk.fileId !== OFFICIAL_RAG_SOURCE.ragFileId) return null

  return {
    documentRevisionId: OFFICIAL_RAG_SOURCE.documentRevisionId,
    title: OFFICIAL_RAG_SOURCE.title,
    anchor: `${OFFICIAL_RAG_SOURCE.sourceRef}#rag-file=${chunk.fileId}&chunk=${chunk.chunkId}`,
    excerpt: context.text,
    sourceDate: OFFICIAL_RAG_SOURCE.sourceDate,
    evidenceId: `rag:${chunk.fileId}:${chunk.chunkId}`,
    source: {
      sourceId: OFFICIAL_RAG_SOURCE.sourceId,
      sourceRef: OFFICIAL_RAG_SOURCE.sourceRef,
      dataDate: OFFICIAL_RAG_SOURCE.sourceDate,
      contentDigest: OFFICIAL_RAG_SOURCE.contentDigest,
    },
  }
}