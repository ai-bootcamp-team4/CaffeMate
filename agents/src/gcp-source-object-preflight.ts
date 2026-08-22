export interface GcpSourceObjectPin {
  sourceUri: string
  gcsObjectGeneration: string
}

export interface SourceObjectPreflightCheck {
  name: 'rag-source-objects'
  ok: boolean
  code: string
  detail?: string
}

interface GcsObjectIdentity {
  bucket: string
  objectName: string
}

interface GcsObjectMetadata {
  bucket?: string
  name?: string
  generation?: string
}

function pass(code: string, detail?: string): SourceObjectPreflightCheck {
  return { name: 'rag-source-objects', ok: true, code, ...(detail ? { detail } : {}) }
}

function fail(code: string, detail?: string): SourceObjectPreflightCheck {
  return { name: 'rag-source-objects', ok: false, code, ...(detail ? { detail } : {}) }
}

function gcsObjectIdentity(sourceUri: string): GcsObjectIdentity | null {
  const match = /^gs:\/\/([^/]+)\/(.+)$/.exec(sourceUri)
  if (!match) return null
  const [, bucket, objectName] = match
  if (!bucket || !objectName) return null
  return { bucket, objectName }
}

export function validGcsSourcePin(source: GcpSourceObjectPin): boolean {
  return Boolean(gcsObjectIdentity(source.sourceUri)) && /^[1-9][0-9]*$/.test(source.gcsObjectGeneration)
}

export async function verifyPinnedSourceObjects(options: {
  fetchImpl: typeof fetch
  token: string
  sources: readonly GcpSourceObjectPin[]
}): Promise<SourceObjectPreflightCheck> {
  for (const source of options.sources) {
    const identity = gcsObjectIdentity(source.sourceUri)
    if (!identity) return fail('RAG_SOURCE_URI_INVALID', source.sourceUri)
    const url = `https://storage.googleapis.com/storage/v1/b/${encodeURIComponent(identity.bucket)}/o/${encodeURIComponent(identity.objectName)}`
    const response = await options.fetchImpl(url, {
      headers: { Authorization: `Bearer ${options.token}` },
    })
    if (!response.ok) {
      return fail('RAG_SOURCE_OBJECT_GET_FAILED', `HTTP ${response.status} ${source.sourceUri}`)
    }
    const metadata = await response.json() as GcsObjectMetadata
    if (metadata.bucket !== identity.bucket || metadata.name !== identity.objectName) {
      return fail('RAG_SOURCE_OBJECT_MISMATCH', source.sourceUri)
    }
    if (metadata.generation !== source.gcsObjectGeneration) {
      return fail(
        'RAG_SOURCE_GENERATION_MISMATCH',
        `${source.sourceUri} expected ${source.gcsObjectGeneration} found ${metadata.generation ?? 'MISSING'}`,
      )
    }
  }
  return pass('RAG_SOURCE_OBJECTS_OK', String(options.sources.length))
}
