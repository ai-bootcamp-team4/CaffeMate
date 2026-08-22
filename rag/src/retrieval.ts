export type RagCorpusKind = 'OFFICIAL' | 'PROJECT'

export interface RagHit {
  documentRevisionId: string
  title: string
  anchor: string
  excerpt: string
  sourceDate: string | null
  evidenceId: string
  source?: {
    sourceId: string
    sourceRef: string
    dataDate: string | null
    contentDigest: string
  }
}

export interface RagBackendRequest {
  corpusKind: RagCorpusKind
  corpusId: string
  query: string
  limit: number
  signal?: AbortSignal
  asOf?: string
  sourceFamilies?: string[]
  documentType?: string | null
  documentRevisionIds?: string[]
  ragFileIds?: string[]
  ventureProjectId?: string
}

export type RagBackend = (request: RagBackendRequest) => Promise<RagHit[]>

export interface ProjectCorpusMapping {
  ventureProjectId: string
  corpusId: string
  documentRevisionIds: string[]
  ragFileIdsByDocumentRevisionId: Record<string, string>
}

export interface ProjectRetrievalInput {
  query: string
  documentRevisionIds: string[]
  limit: number
  documentType?: string | null
  signal?: AbortSignal
}

export interface OfficialRetrievalInput {
  query: string
  sourceFamilies: string[]
  asOf: string
  limit: number
  signal?: AbortSignal
}

export interface RetrievalScope {
  ventureProjectId: string
}

export class RagError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'RagError'
  }
}

export interface RetrievalCoordinatorConfig {
  officialCorpusId?: string
}

export class RetrievalCoordinator {
  constructor(
    private readonly backends: { official?: RagBackend; project?: RagBackend },
    private readonly config: RetrievalCoordinatorConfig = {},
  ) {}

  async retrieveOfficial(input: OfficialRetrievalInput): Promise<RagHit[]> {
    const backend = this.backends.official
    if (!backend) throw new RagError('RAG_UNAVAILABLE', 'official RAG backend is not configured')
    if (!this.config.officialCorpusId) {
      throw new RagError('RAG_UNAVAILABLE', 'official RAG corpus is not configured')
    }
    return backend({
      corpusKind: 'OFFICIAL',
      corpusId: this.config.officialCorpusId,
      query: input.query,
      sourceFamilies: [...input.sourceFamilies],
      asOf: input.asOf,
      limit: input.limit,
      ...(input.signal ? { signal: input.signal } : {}),
    })
  }

  async retrieveProject(input: ProjectRetrievalInput, scope: RetrievalScope, mapping: ProjectCorpusMapping | null): Promise<RagHit[]> {
    if (!mapping || mapping.ventureProjectId !== scope.ventureProjectId) {
      throw new RagError('RAG_SCOPE_MISMATCH', 'project corpus mapping is missing or belongs to a different venture project')
    }

    const allowed = new Set(mapping.documentRevisionIds)
    for (const revisionId of input.documentRevisionIds) {
      if (!allowed.has(revisionId)) {
        throw new RagError('RAG_SCOPE_MISMATCH', `document revision ${revisionId} is outside the project corpus allowlist`)
      }
    }

    const backend = this.backends.project
    if (!backend) throw new RagError('RAG_UNAVAILABLE', 'project RAG backend is not configured')

    const requested = new Set(input.documentRevisionIds)
    const ragFileIds = input.documentRevisionIds.map((revisionId) => mapping.ragFileIdsByDocumentRevisionId[revisionId])
    if (ragFileIds.some((ragFileId) => !ragFileId)) {
      throw new RagError('RAG_SCOPE_MISMATCH', 'every requested document revision must have a pinned RAG file id')
    }
    const hits = await backend({
      corpusKind: 'PROJECT',
      corpusId: mapping.corpusId,
      ventureProjectId: scope.ventureProjectId,
      query: input.query,
      documentType: input.documentType ?? null,
      documentRevisionIds: [...input.documentRevisionIds],
      ragFileIds: ragFileIds as string[],
      limit: input.limit,
      ...(input.signal ? { signal: input.signal } : {}),
    })

    for (const hit of hits) {
      if (!requested.has(hit.documentRevisionId) || !allowed.has(hit.documentRevisionId)) {
        throw new RagError('RAG_SCOPE_MISMATCH', `retrieval hit ${hit.documentRevisionId} escaped the document revision fence`)
      }
    }

    return hits
  }
}
