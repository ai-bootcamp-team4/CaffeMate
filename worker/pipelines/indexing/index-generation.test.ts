import { describe, expect, it } from 'vitest'
import {
  IndexGenerationError,
  activateIndexGeneration,
  applyImportResult,
  assertSearchableGeneration,
  createIndexGeneration,
  failShadowIndexGeneration,
  finalizeIndexGeneration,
  sealIndexGenerationEvaluation,
} from './index-generation'

const sourceRevision = {
  documentRevisionId: 'easylaw-csmSeq-706@2026-07-15',
  ragFileResourceName: 'projects/proj/locations/asia-northeast3/ragCorpora/123/ragFiles/456',
  contentDigest: `sha256:${'a'.repeat(64)}`,
}

function generation() {
  return createIndexGeneration({
    generationId: 'gen-1',
    scope: { kind: 'OFFICIAL' },
    corpusResourceName: 'projects/proj/locations/asia-northeast3/ragCorpora/123',
    parserRevision: 'vertex-layout-parser.v1',
    schemaVersion: 'caffemate.rag-index.v1',
    embeddingModelId: 'text-multilingual-embedding-002',
    rerankerId: 'semantic-ranker-default-004',
    sourceRevisions: [sourceRevision],
  })
}

describe('index generation state', () => {
  it('does not mark an incomplete generation searchable', () => {
    const partial = generation()
    expect(() => finalizeIndexGeneration(partial)).toThrow('INDEX_GENERATION_INCOMPLETE')
    expect(() => assertSearchableGeneration(partial, 'gen-1')).toThrow(IndexGenerationError)
  })

  it('requires BUILDING → EVALUATING → SHADOW → ACTIVE before searchability', () => {
    let current = generation()
    current = applyImportResult(current, { documentRevisionId: sourceRevision.documentRevisionId, status: 'SUCCEEDED' })
    current = finalizeIndexGeneration(current)
    expect(current.status).toBe('EVALUATING')
    expect(() => assertSearchableGeneration(current, 'gen-1')).toThrow('INDEX_GENERATION_NOT_SEARCHABLE')

    current = sealIndexGenerationEvaluation(current, `sha256:${'b'.repeat(64)}`, true)
    expect(current.status).toBe('SHADOW')
    expect(() => assertSearchableGeneration(current, 'gen-1')).toThrow('INDEX_GENERATION_NOT_SEARCHABLE')

    const activated = activateIndexGeneration(current, { generationId: null, version: 7 }, null)
    expect(activated.generation.status).toBe('ACTIVE')
    expect(activated.pointer).toEqual({ generationId: 'gen-1', version: 8 })
    expect(() => assertSearchableGeneration(activated.generation, 'gen-1')).not.toThrow()
  })

  it('fails the generation if any revision import failed', () => {
    let current = generation()
    current = applyImportResult(current, { documentRevisionId: sourceRevision.documentRevisionId, status: 'FAILED', errorCode: 'PARSER_FAILED' })
    expect(finalizeIndexGeneration(current)).toMatchObject({ status: 'FAILED' })
  })

  it('is idempotent for identical import results and rejects conflicting rewrites', () => {
    const first = applyImportResult(generation(), { documentRevisionId: sourceRevision.documentRevisionId, status: 'SUCCEEDED' })
    expect(applyImportResult(first, { documentRevisionId: sourceRevision.documentRevisionId, status: 'SUCCEEDED' })).toEqual(first)
    expect(() => applyImportResult(first, { documentRevisionId: sourceRevision.documentRevisionId, status: 'FAILED', errorCode: 'LATE_FAILURE' })).toThrow('INDEX_IMPORT_RESULT_CONFLICT')
  })

  it('rejects activation without a sealed passing evaluation and rejects a stale compare-and-swap pointer', () => {
    let current = generation()
    current = applyImportResult(current, { documentRevisionId: sourceRevision.documentRevisionId, status: 'SUCCEEDED' })
    current = finalizeIndexGeneration(current)
    expect(() => activateIndexGeneration(current, { generationId: 'old', version: 3 }, 'old')).toThrow('INDEX_GENERATION_TRANSITION_INVALID')

    current = sealIndexGenerationEvaluation(current, `sha256:${'b'.repeat(64)}`, true)
    expect(() => activateIndexGeneration(current, { generationId: 'newer', version: 4 }, 'old')).toThrow('INDEX_GENERATION_POINTER_STALE')
  })

  it('allows SHADOW to fail closed and prevents the failed generation from activating or becoming searchable', () => {
    let current = generation()
    current = applyImportResult(current, { documentRevisionId: sourceRevision.documentRevisionId, status: 'SUCCEEDED' })
    current = finalizeIndexGeneration(current)
    current = sealIndexGenerationEvaluation(current, `sha256:${'b'.repeat(64)}`, true)

    current = failShadowIndexGeneration(current, `sha256:${'c'.repeat(64)}`)
    expect(current).toMatchObject({
      status: 'FAILED',
      evaluationOutcome: 'FAILED',
      sealedEvaluationDigest: `sha256:${'c'.repeat(64)}`,
    })
    expect(() => activateIndexGeneration(current, { generationId: null, version: 0 }, null)).toThrow('INDEX_GENERATION_TRANSITION_INVALID')
    expect(() => assertSearchableGeneration(current, 'gen-1')).toThrow('INDEX_GENERATION_NOT_SEARCHABLE')
  })

  it('requires the authoritative ACTIVE pointer to match the generation being searched', () => {
    let current = generation()
    current = applyImportResult(current, { documentRevisionId: sourceRevision.documentRevisionId, status: 'SUCCEEDED' })
    current = finalizeIndexGeneration(current)
    current = sealIndexGenerationEvaluation(current, `sha256:${'b'.repeat(64)}`, true)
    current = activateIndexGeneration(current, { generationId: null, version: 0 }, null).generation

    expect(() => assertSearchableGeneration(current, 'other-generation')).toThrow('INDEX_GENERATION_POINTER_MISMATCH')
  })

  it('pins immutable release metadata and validates source revision identities', () => {
    const current = generation()
    expect(current).toMatchObject({
      corpusResourceName: 'projects/proj/locations/asia-northeast3/ragCorpora/123',
      parserRevision: 'vertex-layout-parser.v1',
      schemaVersion: 'caffemate.rag-index.v1',
      embeddingModelId: 'text-multilingual-embedding-002',
      rerankerId: 'semantic-ranker-default-004',
      sourceRevisions: [sourceRevision],
      sealedEvaluationDigest: null,
    })

    expect(() => createIndexGeneration({
      generationId: 'gen-invalid',
      scope: { kind: 'OFFICIAL' },
      corpusResourceName: current.corpusResourceName,
      parserRevision: current.parserRevision,
      schemaVersion: current.schemaVersion,
      embeddingModelId: current.embeddingModelId,
      rerankerId: current.rerankerId,
      sourceRevisions: [{ ...sourceRevision, contentDigest: 'not-a-digest' }],
    })).toThrow('INDEX_GENERATION_INPUT_INVALID')
  })
})
