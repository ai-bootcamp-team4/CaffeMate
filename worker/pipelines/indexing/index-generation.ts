export type IndexGenerationStatus = 'BUILDING' | 'READY' | 'FAILED'
export type RevisionImportStatus = 'PENDING' | 'SUCCEEDED' | 'FAILED'

export interface RevisionImportState {
  documentRevisionId: string
  status: RevisionImportStatus
  errorCode?: string
}

export interface IndexGeneration {
  generationId: string
  ventureProjectId: string
  status: IndexGenerationStatus
  revisions: RevisionImportState[]
}

export interface ImportResult {
  documentRevisionId: string
  status: Exclude<RevisionImportStatus, 'PENDING'>
  errorCode?: string
}

export class IndexGenerationError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'IndexGenerationError'
  }
}

export function createIndexGeneration(generationId: string, ventureProjectId: string, documentRevisionIds: string[]): IndexGeneration {
  if (!generationId || !ventureProjectId || documentRevisionIds.length === 0) {
    throw new IndexGenerationError('INDEX_GENERATION_INPUT_INVALID', 'generation, project, and at least one revision are required')
  }
  const unique = new Set(documentRevisionIds)
  if (unique.size !== documentRevisionIds.length) {
    throw new IndexGenerationError('INDEX_GENERATION_DUPLICATE_REVISION', 'document revisions must be unique')
  }
  return {
    generationId,
    ventureProjectId,
    status: 'BUILDING',
    revisions: documentRevisionIds.map((documentRevisionId) => ({ documentRevisionId, status: 'PENDING' })),
  }
}

export function applyImportResult(generation: IndexGeneration, result: ImportResult): IndexGeneration {
  const index = generation.revisions.findIndex((revision) => revision.documentRevisionId === result.documentRevisionId)
  if (index < 0) throw new IndexGenerationError('INDEX_REVISION_NOT_PINNED', `revision ${result.documentRevisionId} is not part of generation ${generation.generationId}`)

  const current = generation.revisions[index]
  if (current.status !== 'PENDING') {
    const identical = current.status === result.status && current.errorCode === result.errorCode
    if (identical) return generation
    throw new IndexGenerationError('INDEX_IMPORT_RESULT_CONFLICT', `revision ${result.documentRevisionId} already has a different terminal result`)
  }

  const revisions = generation.revisions.map((revision, revisionIndex) => revisionIndex === index
    ? { documentRevisionId: result.documentRevisionId, status: result.status, ...(result.errorCode ? { errorCode: result.errorCode } : {}) }
    : revision)

  return { ...generation, revisions }
}

export function finalizeIndexGeneration(generation: IndexGeneration): IndexGeneration {
  if (generation.status !== 'BUILDING') return generation
  if (generation.revisions.some((revision) => revision.status === 'FAILED')) return { ...generation, status: 'FAILED' }
  if (generation.revisions.some((revision) => revision.status === 'PENDING')) {
    throw new IndexGenerationError('INDEX_GENERATION_INCOMPLETE', `generation ${generation.generationId} still has pending revisions`)
  }
  return { ...generation, status: 'READY' }
}

export function assertSearchableGeneration(generation: IndexGeneration): void {
  if (generation.status !== 'READY') {
    throw new IndexGenerationError('INDEX_GENERATION_NOT_SEARCHABLE', `generation ${generation.generationId} is ${generation.status}`)
  }
}
