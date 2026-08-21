import { describe, expect, it } from 'vitest'
import { IndexGenerationError, applyImportResult, assertSearchableGeneration, createIndexGeneration, finalizeIndexGeneration } from './index-generation'

describe('index generation state', () => {
  it('does not mark an incomplete generation searchable', () => {
    const generation = createIndexGeneration('gen-1', 'project-1', ['rev-1', 'rev-2'])
    const partial = applyImportResult(generation, { documentRevisionId: 'rev-1', status: 'SUCCEEDED' })
    expect(() => finalizeIndexGeneration(partial)).toThrow('INDEX_GENERATION_INCOMPLETE')
    expect(() => assertSearchableGeneration(partial)).toThrow(IndexGenerationError)
  })

  it('becomes READY only when every pinned revision succeeded', () => {
    let generation = createIndexGeneration('gen-1', 'project-1', ['rev-1', 'rev-2'])
    generation = applyImportResult(generation, { documentRevisionId: 'rev-1', status: 'SUCCEEDED' })
    generation = applyImportResult(generation, { documentRevisionId: 'rev-2', status: 'SUCCEEDED' })
    const ready = finalizeIndexGeneration(generation)
    expect(ready.status).toBe('READY')
    expect(() => assertSearchableGeneration(ready)).not.toThrow()
  })

  it('fails the generation if any revision import failed', () => {
    let generation = createIndexGeneration('gen-1', 'project-1', ['rev-1'])
    generation = applyImportResult(generation, { documentRevisionId: 'rev-1', status: 'FAILED', errorCode: 'PARSER_FAILED' })
    expect(finalizeIndexGeneration(generation)).toMatchObject({ status: 'FAILED' })
  })

  it('is idempotent for identical import results and rejects conflicting rewrites', () => {
    const generation = createIndexGeneration('gen-1', 'project-1', ['rev-1'])
    const first = applyImportResult(generation, { documentRevisionId: 'rev-1', status: 'SUCCEEDED' })
    expect(applyImportResult(first, { documentRevisionId: 'rev-1', status: 'SUCCEEDED' })).toEqual(first)
    expect(() => applyImportResult(first, { documentRevisionId: 'rev-1', status: 'FAILED', errorCode: 'LATE_FAILURE' })).toThrow('INDEX_IMPORT_RESULT_CONFLICT')
  })
})
