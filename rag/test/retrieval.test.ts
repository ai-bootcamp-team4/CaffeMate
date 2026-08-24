import { describe, expect, it, vi } from 'vitest'
import { RagError, RetrievalCoordinator } from '../src/retrieval'

const scope = { ventureProjectId: 'project-1' }
const projectInput = { query: '임대료', documentRevisionIds: ['docrev-1'], limit: 5 }
const mapping = {
  ventureProjectId: 'project-1',
  corpusId: 'corpus-project-1',
  documentRevisionIds: ['docrev-1', 'docrev-2'],
  ragFileIdsByDocumentRevisionId: {
    'docrev-1': 'rag-file-1',
    'docrev-2': 'rag-file-2',
  },
}
const officialCorpusResource = 'projects/proj-aj20-211200020328/locations/asia-northeast3/ragCorpora/5148740273991319552'

function hit(documentRevisionId = 'docrev-1') {
  return {
    documentRevisionId,
    title: '임대차계약서',
    anchor: 'page:1/table:rent',
    excerpt: '월 임대료 300만원',
    sourceDate: '2026-08-01',
    evidenceId: 'ev-doc-1',
  }
}

describe('RAG retrieval coordinator', () => {
  it('rejects project retrieval before backend execution when no project mapping exists', async () => {
    const backend = vi.fn(async () => [hit()])
    const coordinator = new RetrievalCoordinator({ project: backend, official: backend })

    await expect(coordinator.retrieveProject(projectInput, scope, null)).rejects.toMatchObject({ code: 'RAG_SCOPE_MISMATCH' })
    expect(backend).not.toHaveBeenCalled()
  })

  it('rejects requested document revisions outside the project allowlist', async () => {
    const backend = vi.fn(async () => [hit()])
    const coordinator = new RetrievalCoordinator({ project: backend })

    await expect(coordinator.retrieveProject({ ...projectInput, documentRevisionIds: ['docrev-other'] }, scope, mapping)).rejects.toMatchObject({ code: 'RAG_SCOPE_MISMATCH' })
    expect(backend).not.toHaveBeenCalled()
  })

  it('rejects backend hits that escape the requested revision fence', async () => {
    const coordinator = new RetrievalCoordinator({ project: async () => [hit('docrev-2')] })

    await expect(coordinator.retrieveProject(projectInput, scope, mapping)).rejects.toMatchObject({ code: 'RAG_SCOPE_MISMATCH' })
  })

  it('resolves requested document revisions to server-side RAG file ids before project retrieval', async () => {
    const backend = vi.fn(async () => [hit()])
    const coordinator = new RetrievalCoordinator({ project: backend })

    await coordinator.retrieveProject(projectInput, scope, mapping)

    expect(backend).toHaveBeenCalledWith(expect.objectContaining({
      documentRevisionIds: ['docrev-1'],
      ragFileIds: ['rag-file-1'],
    }))
  })

  it('rejects project retrieval before backend execution when a revision has no pinned RAG file id', async () => {
    const backend = vi.fn(async () => [hit()])
    const coordinator = new RetrievalCoordinator({ project: backend })

    await expect(coordinator.retrieveProject(projectInput, scope, {
      ...mapping,
      ragFileIdsByDocumentRevisionId: {},
    })).rejects.toMatchObject({ code: 'RAG_SCOPE_MISMATCH' })
    expect(backend).not.toHaveBeenCalled()
  })

  it('does not silently fall back when the required backend is unavailable', async () => {
    const fallback = vi.fn(async () => [hit()])
    const coordinator = new RetrievalCoordinator({ official: fallback })

    await expect(coordinator.retrieveProject(projectInput, scope, mapping)).rejects.toBeInstanceOf(RagError)
    await expect(coordinator.retrieveProject(projectInput, scope, mapping)).rejects.toMatchObject({ code: 'RAG_UNAVAILABLE' })
    expect(fallback).not.toHaveBeenCalled()
  })

  it('routes official retrieval only to the official corpus backend', async () => {
    const official = vi.fn(async () => [hit('official-rev-1')])
    const project = vi.fn(async () => [hit()])
    const coordinator = new RetrievalCoordinator({ official, project }, { officialCorpusId: officialCorpusResource })

    const result = await coordinator.retrieveOfficial({ query: '가맹사업법', sourceFamilies: ['LAW'], asOf: '2026-08-21', limit: 5 })

    expect(result).toHaveLength(1)
    expect(official).toHaveBeenCalledWith(expect.objectContaining({
      corpusKind: 'OFFICIAL',
      corpusId: officialCorpusResource,
    }))
    expect(project).not.toHaveBeenCalled()
  })

  it('pins official retrieval to indexed RAG files when the requested source family has a complete registry', async () => {
    const official = vi.fn(async () => [hit('official-rev-1')])
    const coordinator = new RetrievalCoordinator({ official }, {
      officialCorpusId: officialCorpusResource,
      officialRagFileIdsBySourceFamily: {
        COMPANY_OFFICIAL_FRANCHISE: ['compose-eligibility', 'compose-opening-cost'],
      },
    })

    await coordinator.retrieveOfficial({
      query: '컴포즈커피 개인 가맹 가능 여부',
      sourceFamilies: ['COMPANY_OFFICIAL_FRANCHISE'],
      asOf: '2026-08-25',
      limit: 3,
    })

    expect(official).toHaveBeenCalledWith(expect.objectContaining({
      ragFileIds: ['compose-eligibility', 'compose-opening-cost'],
    }))
  })

  it('threads caller cancellation to the official backend request', async () => {
    const controller = new AbortController()
    const official = vi.fn(async () => [hit('official-rev-1')])
    const coordinator = new RetrievalCoordinator({ official }, { officialCorpusId: officialCorpusResource })

    await coordinator.retrieveOfficial({
      query: '가맹사업법',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 5,
      signal: controller.signal,
    })

    expect(official).toHaveBeenCalledWith(expect.objectContaining({ signal: controller.signal }))
  })

  it('rejects official retrieval before backend execution when no authoritative corpus mapping is configured', async () => {
    const official = vi.fn(async () => [hit('official-rev-1')])
    const coordinator = new RetrievalCoordinator({ official })

    await expect(coordinator.retrieveOfficial({
      query: '가맹사업법',
      sourceFamilies: ['LAW'],
      asOf: '2026-08-21',
      limit: 5,
    })).rejects.toMatchObject({ code: 'RAG_UNAVAILABLE' })
    expect(official).not.toHaveBeenCalled()
  })
})
