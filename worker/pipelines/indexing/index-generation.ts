export const INDEX_PIPELINE_RELEASE = Object.freeze({
  parserRevision: 'vertex-layout-parser.v1',
  schemaVersion: 'caffemate.rag-index.v1',
  embeddingModelId: 'text-multilingual-embedding-002',
  rerankerId: 'semantic-ranker-default-004',
} as const)

export type IndexGenerationStatus = 'BUILDING' | 'EVALUATING' | 'SHADOW' | 'ACTIVE' | 'FAILED'
export type RevisionImportStatus = 'PENDING' | 'SUCCEEDED' | 'FAILED'
export type IndexEvaluationOutcome = 'PASSED' | 'FAILED' | null

export type IndexGenerationScope =
  | { kind: 'OFFICIAL' }
  | { kind: 'PROJECT'; ventureProjectId: string }

export interface SourceRevisionPin {
  documentRevisionId: string
  ragFileResourceName: string
  contentDigest: string
}

export interface RevisionImportState {
  documentRevisionId: string
  status: RevisionImportStatus
  errorCode?: string
}

export interface CreateIndexGenerationInput {
  generationId: string
  scope: IndexGenerationScope
  corpusResourceName: string
  parserRevision: string
  schemaVersion: string
  embeddingModelId: string
  rerankerId: string
  sourceRevisions: SourceRevisionPin[]
}

export interface IndexGeneration extends CreateIndexGenerationInput {
  status: IndexGenerationStatus
  revisions: RevisionImportState[]
  sealedEvaluationDigest: string | null
  evaluationOutcome: IndexEvaluationOutcome
}

export interface IndexGenerationPointer {
  generationId: string | null
  version: number
}

export interface ImportResult {
  documentRevisionId: string
  status: Exclude<RevisionImportStatus, 'PENDING'>
  errorCode?: string
}

export interface IndexActivationResult {
  generation: IndexGeneration
  pointer: IndexGenerationPointer
}

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/
const CORPUS_RESOURCE = /^projects\/[^/]+\/locations\/[^/]+\/ragCorpora\/[^/]+$/

export class IndexGenerationError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'IndexGenerationError'
  }
}

function invalidInput(message: string): never {
  throw new IndexGenerationError('INDEX_GENERATION_INPUT_INVALID', message)
}

function requireNonEmpty(value: string, field: string): void {
  if (!value.trim()) invalidInput(`${field} is required`)
}

function validateScope(scope: IndexGenerationScope): void {
  if (scope.kind === 'PROJECT') requireNonEmpty(scope.ventureProjectId, 'scope.ventureProjectId')
}

function validateSourceRevisions(corpusResourceName: string, sourceRevisions: SourceRevisionPin[]): void {
  if (sourceRevisions.length === 0) invalidInput('at least one source revision is required')

  const revisionIds = new Set<string>()
  const ragFiles = new Set<string>()
  for (const revision of sourceRevisions) {
    requireNonEmpty(revision.documentRevisionId, 'sourceRevisions.documentRevisionId')
    requireNonEmpty(revision.ragFileResourceName, 'sourceRevisions.ragFileResourceName')
    if (!SHA256_DIGEST.test(revision.contentDigest)) invalidInput('source revision contentDigest must be sha256')
    if (!revision.ragFileResourceName.startsWith(`${corpusResourceName}/ragFiles/`)) {
      invalidInput('source revision RAG file must belong to the pinned corpus')
    }
    if (revisionIds.has(revision.documentRevisionId)) {
      throw new IndexGenerationError('INDEX_GENERATION_DUPLICATE_REVISION', 'document revisions must be unique')
    }
    if (ragFiles.has(revision.ragFileResourceName)) {
      throw new IndexGenerationError('INDEX_GENERATION_DUPLICATE_RAG_FILE', 'RAG file resources must be unique')
    }
    revisionIds.add(revision.documentRevisionId)
    ragFiles.add(revision.ragFileResourceName)
  }
}

export function createIndexGeneration(input: CreateIndexGenerationInput): IndexGeneration {
  requireNonEmpty(input.generationId, 'generationId')
  validateScope(input.scope)
  if (!CORPUS_RESOURCE.test(input.corpusResourceName)) invalidInput('corpusResourceName is invalid')
  requireNonEmpty(input.parserRevision, 'parserRevision')
  requireNonEmpty(input.schemaVersion, 'schemaVersion')
  requireNonEmpty(input.embeddingModelId, 'embeddingModelId')
  requireNonEmpty(input.rerankerId, 'rerankerId')
  validateSourceRevisions(input.corpusResourceName, input.sourceRevisions)

  return {
    ...input,
    scope: { ...input.scope },
    sourceRevisions: input.sourceRevisions.map((revision) => ({ ...revision })),
    status: 'BUILDING',
    revisions: input.sourceRevisions.map(({ documentRevisionId }) => ({ documentRevisionId, status: 'PENDING' })),
    sealedEvaluationDigest: null,
    evaluationOutcome: null,
  }
}

export function applyImportResult(generation: IndexGeneration, result: ImportResult): IndexGeneration {
  const index = generation.revisions.findIndex((revision) => revision.documentRevisionId === result.documentRevisionId)
  if (index < 0) {
    throw new IndexGenerationError(
      'INDEX_REVISION_NOT_PINNED',
      `revision ${result.documentRevisionId} is not part of generation ${generation.generationId}`,
    )
  }

  const current = generation.revisions[index]
  if (current.status !== 'PENDING') {
    const identical = current.status === result.status && current.errorCode === result.errorCode
    if (identical) return generation
    throw new IndexGenerationError(
      'INDEX_IMPORT_RESULT_CONFLICT',
      `revision ${result.documentRevisionId} already has a different terminal result`,
    )
  }
  if (generation.status !== 'BUILDING') {
    throw new IndexGenerationError(
      'INDEX_GENERATION_TRANSITION_INVALID',
      `cannot apply import results while generation ${generation.generationId} is ${generation.status}`,
    )
  }

  const revisions = generation.revisions.map((revision, revisionIndex) => revisionIndex === index
    ? {
        documentRevisionId: result.documentRevisionId,
        status: result.status,
        ...(result.errorCode ? { errorCode: result.errorCode } : {}),
      }
    : revision)

  return { ...generation, revisions }
}

export function finalizeIndexGeneration(generation: IndexGeneration): IndexGeneration {
  if (generation.status === 'EVALUATING' || generation.status === 'FAILED') return generation
  if (generation.status !== 'BUILDING') {
    throw new IndexGenerationError(
      'INDEX_GENERATION_TRANSITION_INVALID',
      `cannot finalize imports while generation ${generation.generationId} is ${generation.status}`,
    )
  }
  if (generation.revisions.some((revision) => revision.status === 'FAILED')) {
    return { ...generation, status: 'FAILED' }
  }
  if (generation.revisions.some((revision) => revision.status === 'PENDING')) {
    throw new IndexGenerationError(
      'INDEX_GENERATION_INCOMPLETE',
      `generation ${generation.generationId} still has pending revisions`,
    )
  }
  return { ...generation, status: 'EVALUATING' }
}

export function sealIndexGenerationEvaluation(
  generation: IndexGeneration,
  sealedEvaluationDigest: string,
  passed: boolean,
): IndexGeneration {
  if (!SHA256_DIGEST.test(sealedEvaluationDigest)) {
    throw new IndexGenerationError('INDEX_EVALUATION_DIGEST_INVALID', 'sealed evaluation digest must be sha256')
  }

  if (generation.status === 'SHADOW') {
    if (generation.sealedEvaluationDigest === sealedEvaluationDigest && generation.evaluationOutcome === 'PASSED' && passed) {
      return generation
    }
    throw new IndexGenerationError('INDEX_EVALUATION_RESULT_CONFLICT', 'generation already has a different sealed evaluation')
  }
  if (generation.status === 'FAILED' && generation.sealedEvaluationDigest === sealedEvaluationDigest) {
    if (generation.evaluationOutcome === 'FAILED' && !passed) return generation
    throw new IndexGenerationError('INDEX_EVALUATION_RESULT_CONFLICT', 'generation already has a different sealed evaluation')
  }
  if (generation.status !== 'EVALUATING') {
    throw new IndexGenerationError(
      'INDEX_GENERATION_TRANSITION_INVALID',
      `cannot seal evaluation while generation ${generation.generationId} is ${generation.status}`,
    )
  }

  return {
    ...generation,
    status: passed ? 'SHADOW' : 'FAILED',
    sealedEvaluationDigest,
    evaluationOutcome: passed ? 'PASSED' : 'FAILED',
  }
}

export function failShadowIndexGeneration(
  generation: IndexGeneration,
  sealedEvaluationDigest: string,
): IndexGeneration {
  if (!SHA256_DIGEST.test(sealedEvaluationDigest)) {
    throw new IndexGenerationError('INDEX_EVALUATION_DIGEST_INVALID', 'sealed evaluation digest must be sha256')
  }
  if (generation.status === 'FAILED'
    && generation.evaluationOutcome === 'FAILED'
    && generation.sealedEvaluationDigest === sealedEvaluationDigest) {
    return generation
  }
  if (generation.status !== 'SHADOW') {
    throw new IndexGenerationError(
      'INDEX_GENERATION_TRANSITION_INVALID',
      `cannot fail shadow evaluation while generation ${generation.generationId} is ${generation.status}`,
    )
  }

  return {
    ...generation,
    status: 'FAILED',
    sealedEvaluationDigest,
    evaluationOutcome: 'FAILED',
  }
}

export function activateIndexGeneration(
  generation: IndexGeneration,
  pointer: IndexGenerationPointer,
  expectedCurrentGenerationId: string | null,
): IndexActivationResult {
  if (!Number.isSafeInteger(pointer.version) || pointer.version < 0) {
    throw new IndexGenerationError('INDEX_GENERATION_POINTER_INVALID', 'pointer version must be a non-negative safe integer')
  }
  if (generation.status === 'ACTIVE' && pointer.generationId === generation.generationId) {
    return { generation, pointer }
  }
  if (generation.status !== 'SHADOW' || generation.evaluationOutcome !== 'PASSED' || !generation.sealedEvaluationDigest) {
    throw new IndexGenerationError(
      'INDEX_GENERATION_TRANSITION_INVALID',
      `generation ${generation.generationId} must be SHADOW with a sealed passing evaluation before activation`,
    )
  }
  if (pointer.generationId !== expectedCurrentGenerationId) {
    throw new IndexGenerationError(
      'INDEX_GENERATION_POINTER_STALE',
      `expected current generation ${expectedCurrentGenerationId ?? 'null'} but found ${pointer.generationId ?? 'null'}`,
    )
  }

  return {
    generation: { ...generation, status: 'ACTIVE' },
    pointer: { generationId: generation.generationId, version: pointer.version + 1 },
  }
}

export function assertSearchableGeneration(generation: IndexGeneration, activeGenerationId: string | null): void {
  if (generation.status !== 'ACTIVE' || generation.evaluationOutcome !== 'PASSED' || !generation.sealedEvaluationDigest) {
    throw new IndexGenerationError(
      'INDEX_GENERATION_NOT_SEARCHABLE',
      `generation ${generation.generationId} is ${generation.status}`,
    )
  }
  if (activeGenerationId !== generation.generationId) {
    throw new IndexGenerationError(
      'INDEX_GENERATION_POINTER_MISMATCH',
      `active pointer ${activeGenerationId ?? 'null'} does not match generation ${generation.generationId}`,
    )
  }
}