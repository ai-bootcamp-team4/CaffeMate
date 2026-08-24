import { describe, expect, it } from 'vitest'
import {
  createOfficialRagContextMapper,
  mapOfficialRagContext,
  OFFICIAL_RAG_SOURCE,
  pinnedRagFileIdsBySourceFamily,
  type OfficialRagSource,
} from '../src/official-rag'

const request = {
  corpusKind: 'OFFICIAL' as const,
  corpusId: 'official-corpus',
  query: '커피전문점 영업신고',
  sourceFamilies: ['GOVERNMENT_GUIDE'],
  asOf: '2026-07-15',
  limit: 3,
}

describe('official RAG source mapping', () => {
  it('enables exact family routing only after every prepared source has a pinned RAG file ID', () => {
    const source: OfficialRagSource = {
      ...OFFICIAL_RAG_SOURCE,
      sourceFamily: 'COMPANY_OFFICIAL_FRANCHISE',
      ragFileId: 'file-1',
    }

    expect(pinnedRagFileIdsBySourceFamily([
      source,
      { ...source, sourceId: 'pending', ragFileId: null },
    ])).toEqual({})
    expect(pinnedRagFileIdsBySourceFamily([
      source,
      { ...source, sourceId: 'ready', ragFileId: 'file-2' },
    ])).toEqual({ COMPANY_OFFICIAL_FRANCHISE: ['file-1', 'file-2'] })
  })

  it('pins the imported GCS object generation as part of the source revision identity', () => {
    expect(OFFICIAL_RAG_SOURCE.gcsGeneration).toBe('1787329995006379')
  })

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
      sourceFamily: 'GOVERNMENT_GUIDE',
      claimType: 'CAFE_OPENING_REQUIRED_PROCEDURES',
      sourceId: 'easylaw-csmSeq-706',
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

  it('preserves franchise brand, source family, claim type, source URL, and checked date', () => {
    const source: OfficialRagSource = {
      sourceId: 'compose-official-opening-cost',
      brandId: 'kr-compose-coffee',
      sourceFamily: 'COMPANY_OFFICIAL_FRANCHISE',
      claimType: 'FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE',
      documentRevisionId: 'compose-official-opening-cost@2026-08-25',
      title: '컴포즈커피 공식 창업비 안내',
      sourceDate: '2026-08-25',
      sourceRef: 'https://composecoffee.com/composefranchise',
      sourceUri: 'gs://bucket/official/franchise/kr-compose-coffee/opening-cost.md',
      ragFileId: 'rag-compose-cost',
      contentDigest: `sha256:${'a'.repeat(64)}`,
    }
    const mapper = createOfficialRagContextMapper([source])

    expect(mapper({
      sourceUri: source.sourceUri,
      sourceDisplayName: 'opening-cost.md',
      text: '10평 기준 공식 창업비 안내입니다.',
      chunk: { fileId: source.ragFileId, chunkId: 'chunk-cost' },
    }, {
      ...request,
      sourceFamilies: ['COMPANY_OFFICIAL_FRANCHISE'],
      asOf: '2026-08-25',
    })).toMatchObject({
      documentRevisionId: source.documentRevisionId,
      sourceFamily: source.sourceFamily,
      claimType: source.claimType,
      brandId: source.brandId,
      source: {
        sourceId: source.sourceId,
        sourceRef: source.sourceRef,
        dataDate: source.sourceDate,
      },
    })
  })

  it('does not map a prepared source before Vertex returns a pinned RAG file id', () => {
    const source: OfficialRagSource = {
      sourceId: 'ediya-official-eligibility',
      brandId: 'kr-ediya-coffee',
      sourceFamily: 'COMPANY_OFFICIAL_FRANCHISE',
      claimType: 'FRANCHISE_INDIVIDUAL_ELIGIBILITY',
      documentRevisionId: 'ediya-official-eligibility@2026-08-25',
      title: '이디야커피 공식 가맹 안내',
      sourceDate: '2026-08-25',
      sourceRef: 'https://www.ediya.com/C/contents/franchise_02.html',
      sourceUri: 'gs://bucket/official/franchise/kr-ediya-coffee/eligibility.md',
      ragFileId: null,
      contentDigest: `sha256:${'b'.repeat(64)}`,
    }
    const mapper = createOfficialRagContextMapper([source])

    expect(mapper({
      sourceUri: source.sourceUri,
      sourceDisplayName: 'eligibility.md',
      text: '가맹 안내',
      chunk: { fileId: 'untrusted-file-id', chunkId: 'chunk-1' },
    }, {
      ...request,
      sourceFamilies: ['COMPANY_OFFICIAL_FRANCHISE'],
      asOf: '2026-08-25',
    })).toBeNull()
  })
})
