import { createHmac } from 'node:crypto'
import { GoogleAuth } from 'google-auth-library'
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client'
import { MCP_PRODUCTION_TOOL_NAMES } from './manifest'

function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name}_REQUIRED`)
  return value
}

function scopeToken(secret: string): string {
  const now = Math.floor(Date.now() / 1000)
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url')
  const payload = Buffer.from(JSON.stringify({
    iss: 'caffemate-control-api', aud: 'caffemate-mcp',
    venture_project_id: 'mcp-smoke-project', workflow_run_id: 'mcp-smoke-workflow',
    full_head_digest: `sha256:${'a'.repeat(64)}`, jti: `smoke-${now}`, iat: now, exp: now + 300,
  })).toString('base64url')
  const signature = createHmac('sha256', secret).update(`${header}.${payload}`).digest('base64url')
  return `${header}.${payload}.${signature}`
}

const baseUrl = required('MCP_BASE_URL')
const secret = required('MCP_SCOPE_HMAC_SECRET')
const idClient = await new GoogleAuth().getIdTokenClient(baseUrl)
const identityHeaders = await idClient.getRequestHeaders(baseUrl)
const authorization = identityHeaders.get('authorization')
if (!authorization) throw new Error('MCP_SMOKE_IDENTITY_TOKEN_MISSING')
const token = scopeToken(secret)

const authenticatedFetch: typeof fetch = async (input, init) => {
  const headers = new Headers(init?.headers)
  headers.set('Authorization', authorization)
  headers.set('X-CaffeMate-Scope-Token', token)
  return fetch(input, { ...init, headers })
}

const client = new Client(
  { name: 'caffemate-control-api', version: '1.0.0' },
  { versionNegotiation: { mode: { pin: '2026-07-28' } } },
)
try {
  await client.connect(new StreamableHTTPClientTransport(new URL(`${baseUrl}/mcp`), { fetch: authenticatedFetch }))
  const listed = await client.listTools()
  const names = listed.tools.map((tool) => tool.name)
  if (names.length !== MCP_PRODUCTION_TOOL_NAMES.length
    || MCP_PRODUCTION_TOOL_NAMES.some((name) => !names.includes(name))) {
    throw new Error('MCP_MANIFEST_MISMATCH')
  }
  const result = await client.callTool({
    name: 'resolve_area', arguments: { query: '서울특별시 마포구 망원동', country_code: 'KR', limit: 5 },
  })
  const structured = result.structuredContent as {
    status?: string
    data?: Array<{ administrative_code?: string; boundary_version?: string }>
    source_trace?: Array<{ source_id?: string; content_digest?: string }>
    error_codes?: string[]
    missing_fields?: string[]
  } | undefined
  if (!structured || structured.status !== 'OK') {
    throw new Error(`MCP_RESOLVE_AREA_UNSAFE_OUTCOME status=${structured?.status ?? 'MISSING'} errors=${structured?.error_codes?.join(',') ?? ''} missing=${structured?.missing_fields?.join(',') ?? ''}`)
  }
  const administrativeCode = structured.data?.[0]?.administrative_code
  const boundaryVersion = structured.data?.[0]?.boundary_version
  if (!administrativeCode
    || !boundaryVersion
    || !structured.source_trace?.some((row) => row.source_id === 'mois-legal-dong-directory'
      && row.content_digest?.startsWith('sha256:'))) {
    throw new Error('MCP_LOCAL_AREA_DIRECTORY_UNVERIFIED')
  }

  const areaResult = await client.callTool({
    name: 'get_area_profile',
    arguments: {
      administrative_code: administrativeCode,
      boundary_version: boundaryVersion,
      as_of: '2026-08-23',
    },
  })
  const area = areaResult.structuredContent as {
    status?: string
    data?: Array<{ metric?: string }>
    source_trace?: Array<{ source_id?: string; content_digest?: string }>
  } | undefined
  const areaMetrics = new Set(area?.data?.map((row) => row.metric))
  if (area?.status !== 'OK'
    || !areaMetrics.has('MAPPED_ADMIN_DONG_COUNT')
    || !areaMetrics.has('RESIDENT_POPULATION')
    || !area?.source_trace?.some((row) => row.source_id === 'mois-admin-legal-mapping'
      && row.content_digest?.startsWith('sha256:'))) {
    throw new Error(`MCP_AREA_GROUNDING_UNSAFE_OUTCOME ${JSON.stringify(area)}`)
  }

  const cafeResult = await client.callTool({
    name: 'search_cafe_observations',
    arguments: {
      administrative_code: administrativeCode,
      boundary_version: boundaryVersion,
      as_of: '2026-08-23',
      metrics: ['CAFE_COUNT', 'CLOSE_COUNT', 'ESTIMATED_SALES', 'FOOT_TRAFFIC'],
    },
  })
  const cafe = cafeResult.structuredContent as {
    status?: string
    data?: Array<{ metric?: string }>
    source_trace?: Array<{ source_id?: string; content_digest?: string }>
  } | undefined
  const cafeMetrics = new Set(cafe?.data?.map((row) => row.metric))
  const cafeSources = new Set(cafe?.source_trace?.map((row) => row.source_id))
  if (cafe?.status !== 'OK'
    || !['CAFE_COUNT', 'CLOSE_COUNT', 'ESTIMATED_SALES', 'FOOT_TRAFFIC']
      .every((metric) => cafeMetrics.has(metric))
    || !['seoul-cafe-store-quarterly', 'seoul-cafe-sales-quarterly', 'seoul-foot-traffic-quarterly']
      .every((source) => cafeSources.has(source))
    || !cafe?.source_trace?.every((row) => row.content_digest?.startsWith('sha256:'))) {
    throw new Error(`MCP_CAFE_GROUNDING_UNSAFE_OUTCOME ${JSON.stringify(cafe)}`)
  }

  const officialResult = await client.callTool({
    name: 'retrieve_official_documents',
    arguments: {
      query: '커피전문점 영업신고',
      source_families: ['GOVERNMENT_GUIDE'],
      as_of: '2026-07-15',
      limit: 3,
    },
  })
  const official = officialResult.structuredContent as {
    status?: string
    data?: Array<{ document_revision_id?: string }>
  } | undefined
  if (official?.status !== 'OK'
    || !official.data?.some((row) => row.document_revision_id === 'easylaw-csmSeq-706@2026-07-15')) {
    throw new Error(`MCP_OFFICIAL_RAG_UNSAFE_OUTCOME ${JSON.stringify({
      isError: officialResult.isError,
      structuredContent: officialResult.structuredContent,
      content: officialResult.content,
    })}`)
  }

  const healthResult = await client.callTool({
    name: 'get_source_health',
    arguments: {
      source_ids: ['easylaw-csmSeq-706'],
      as_of: '2026-07-15',
    },
  })
  const health = healthResult.structuredContent as {
    status?: string
    data?: Array<{ source_id?: string; status?: string }>
    error_codes?: string[]
  } | undefined
  const healthySources = new Set(
    health?.data
      ?.filter((row) => row.status === 'HEALTHY')
      .map((row) => row.source_id),
  )
  if (health?.status !== 'OK'
    || !healthySources.has('easylaw-csmSeq-706')) {
    throw new Error(`MCP_SOURCE_HEALTH_UNSAFE_OUTCOME ${JSON.stringify({
      isError: healthResult.isError,
      status: health?.status,
      data: health?.data,
      error_codes: health?.error_codes,
    })}`)
  }

  const badHeaders = new Headers({ Authorization: authorization })
  badHeaders.set('X-CaffeMate-Scope-Token', 'invalid')
  const badScope = await fetch(`${baseUrl}/mcp`, { method: 'POST', headers: badHeaders, body: '{}' })
  if (badScope.status !== 403) throw new Error(`MCP_BAD_SCOPE_NOT_REJECTED_${badScope.status}`)
  console.log(`MCP_SMOKE_OK tools=${names.length} resolve_area=${structured.status} area_grounding=${area.status} cafe_grounding=${cafe.status} official_rag=${official.status} source_health=${health.status} invalid_scope=403`)
} finally {
  await client.close().catch(() => undefined)
}
