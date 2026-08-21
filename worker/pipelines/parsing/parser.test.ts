import { describe, expect, it } from 'vitest'
import { ParsingError, buildParserBlockSet, chunkParserBlocks } from './parser'

const anchor = { document_revision_id: 'rev-1', page_index: 0, section_path: '임대조건', table_id: null, row: null, column: null, bbox: null }

describe('parser block boundary', () => {
  it('preserves source text and anchor data exactly', () => {
    const blocks = [{ block_id: 'b-1', text: '  월 임대료 300만원  ', anchor }]
    const set = buildParserBlockSet('doc-1', 'rev-1', blocks)
    expect(set.blocks[0]).toEqual(blocks[0])
  })

  it('rejects blocks from another document revision', () => {
    const blocks = [{ block_id: 'b-1', text: 'text', anchor: { ...anchor, document_revision_id: 'rev-2' } }]
    expect(() => buildParserBlockSet('doc-1', 'rev-1', blocks)).toThrow(ParsingError)
    expect(() => buildParserBlockSet('doc-1', 'rev-1', blocks)).toThrow('PARSER_REVISION_MISMATCH')
  })

  it('rejects duplicate block ids', () => {
    const blocks = [
      { block_id: 'b-1', text: 'first', anchor },
      { block_id: 'b-1', text: 'second', anchor: { ...anchor, page_index: 1 } },
    ]
    expect(() => buildParserBlockSet('doc-1', 'rev-1', blocks)).toThrow('PARSER_DUPLICATE_BLOCK_ID')
  })

  it('creates deterministic document-analysis batches of at most 12 blocks', () => {
    const blocks = Array.from({ length: 25 }, (_, index) => ({
      block_id: `b-${index + 1}`,
      text: `text-${index + 1}`,
      anchor: { ...anchor, page_index: index },
    }))
    const set = buildParserBlockSet('doc-1', 'rev-1', blocks)
    const batches = chunkParserBlocks(set)
    expect(batches.map((batch) => batch.blocks.length)).toEqual([12, 12, 1])
    expect(batches.flatMap((batch) => batch.blocks.map((block) => block.block_id))).toEqual(blocks.map((block) => block.block_id))
  })
})
