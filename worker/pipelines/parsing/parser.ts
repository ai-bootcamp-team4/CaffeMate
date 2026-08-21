export interface DocumentAnchor {
  document_revision_id: string
  page_index: number
  section_path?: string | null
  table_id?: string | null
  row?: number | null
  column?: number | null
  bbox?: number[] | null
}

export interface ParserBlock {
  block_id: string
  text: string
  anchor: DocumentAnchor
}

export interface ParserBlockSet {
  documentId: string
  documentRevisionId: string
  blocks: ParserBlock[]
}

export interface ParserBlockBatch {
  documentId: string
  documentRevisionId: string
  batchIndex: number
  blocks: ParserBlock[]
}

export class ParsingError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'ParsingError'
  }
}

function validateBlock(block: ParserBlock, revisionId: string): void {
  if (!block.block_id) throw new ParsingError('PARSER_BLOCK_ID_REQUIRED', 'parser block id must be non-empty')
  if (block.anchor.document_revision_id !== revisionId) {
    throw new ParsingError('PARSER_REVISION_MISMATCH', `block ${block.block_id} belongs to ${block.anchor.document_revision_id}`)
  }
  if (!Number.isInteger(block.anchor.page_index) || block.anchor.page_index < 0) {
    throw new ParsingError('PARSER_ANCHOR_INVALID', `block ${block.block_id} has an invalid page index`)
  }
}

export function buildParserBlockSet(documentId: string, documentRevisionId: string, blocks: ParserBlock[]): ParserBlockSet {
  if (!documentId || !documentRevisionId) throw new ParsingError('PARSER_DOCUMENT_ID_REQUIRED', 'document and revision ids are required')

  const seen = new Set<string>()
  for (const block of blocks) {
    validateBlock(block, documentRevisionId)
    if (seen.has(block.block_id)) throw new ParsingError('PARSER_DUPLICATE_BLOCK_ID', `duplicate block id ${block.block_id}`)
    seen.add(block.block_id)
  }

  return { documentId, documentRevisionId, blocks: [...blocks] }
}

export function chunkParserBlocks(blockSet: ParserBlockSet, maxBlocks = 12): ParserBlockBatch[] {
  if (!Number.isInteger(maxBlocks) || maxBlocks < 1 || maxBlocks > 12) {
    throw new ParsingError('PARSER_BATCH_SIZE_INVALID', 'Document Analyst batches must contain between 1 and 12 blocks')
  }

  const batches: ParserBlockBatch[] = []
  for (let offset = 0; offset < blockSet.blocks.length; offset += maxBlocks) {
    batches.push({
      documentId: blockSet.documentId,
      documentRevisionId: blockSet.documentRevisionId,
      batchIndex: batches.length,
      blocks: blockSet.blocks.slice(offset, offset + maxBlocks),
    })
  }
  return batches
}
