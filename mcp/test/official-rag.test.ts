import { describe, expect, it } from 'vitest'
import { mapOfficialRagContext, OFFICIAL_RAG_SOURCE } from '../src/official-rag'

const request = {
  corpusKind: 'OFFICIAL' as const,
  corpusId: 'official-corpus',
  query: '커피전문점 영업신고',
  sourceFamilies: ['GOVERNMENT_GUIDE'],
  asOf: '2026-07-15',
  limit: 3,
}

describe('official RAG source mapping', () => {
  it('maps only the pinned GCS source and exact RAG file/chunk identity', () => {
    const mapped = mapOfficialRagContext({
      sourceUri: OFFICIAL_RAG_SOURCE.sourceUri,
      sourceDisplayName: 'source.html',
      text: '커피전문점은 휴게음식점 영업신고를 해야 합니다.',
      chunk: { fileId: OFFICIAL_RAG_SOURCE.ragFileId, chunkId: '5769839172020912571' },
      score: 0.15,
    }, request)

    expect(mapped).toEqual({
      documentRevisionId: 'easylaw-csmSeq-706@2026-07-15',
      title: '커피전문점 영업신고 및 사업자등록',
      anchor: `${OFFICIAL_RAG_SOURCE.sourceRef}#rag-file=${OFFICIAL_RAG_SOURCE.ragFileId}&chunk=5769839172020912571`,
      excerpt: '커피전문점은 휴게음식점 영업신고를 해야 합니다.',
      sourceDate: '2026-07-15',
      evidenceId: `rag:${OFFICIAL_RAG_SOURCE.ragFileId}:5769839172020912571`,
      source: {
        sourceId: 'easylaw-csmSeq-706',
        sourceRef: OFFICIAL_RAG_SOURCE.sourceRef,
        dataDate: '2026-07-15',
        contentDigest: 'sha256:f44af895c9dd771ba22d3890016928ba8bfaa3ed2306d9cd0a5b5bb6ee9d9c34',
      },
    })
  })

  it.each([
    ['unknown source', 'gs://other/source.html', OFFICIAL_RAG_SOURCE.ragFileId, 'chunk-1'],
    ['wrong file', OFFICIAL_RAG_SOURCE.sourceUri, '999', 'chunk-1'],
    ['missing chunk', OFFICIAL_RAG_SOURCE.sourceUri, OFFICIAL_RAG_SOURCE.ragFileId, ''],
  ])('fails closed for %s', (_label, sourceUri, fileId, chunkId) => {
    expect(mapOfficialRagContext({
      sourceUri,
      sourceDisplayName: 'source.html',
      text: 'text',
      chunk: { fileId, chunkId },
    }, request)).toBeNull()
  })

  it('never maps a project-corpus request through the public source catalog', () => {
    expect(mapOfficialRagContext({
      sourceUri: OFFICIAL_RAG_SOURCE.sourceUri,
      sourceDisplayName: 'source.html',
      text: 'text',
      chunk: { fileId: OFFICIAL_RAG_SOURCE.ragFileId, chunkId: 'chunk-1' },
    }, { ...request, corpusKind: 'PROJECT', ventureProjectId: 'project-1' })).toBeNull()
  })

  it('fails closed when the pinned source family is outside the requested family fence', () => {
    expect(mapOfficialRagContext({
      sourceUri: OFFICIAL_RAG_SOURCE.sourceUri,
      sourceDisplayName: 'source.html',
      text: 'text',
      chunk: { fileId: OFFICIAL_RAG_SOURCE.ragFileId, chunkId: 'chunk-1' },
    }, { ...request, sourceFamilies: ['LAW'] })).toBeNull()
  })

  it('fails closed when the pinned source revision is newer than the requested as-of fence', () => {
    expect(mapOfficialRagContext({
      sourceUri: OFFICIAL_RAG_SOURCE.sourceUri,
      sourceDisplayName: 'source.html',
      text: 'text',
      chunk: { fileId: OFFICIAL_RAG_SOURCE.ragFileId, chunkId: 'chunk-1' },
    }, { ...request, asOf: '2020-01-01' })).toBeNull()
  })
})