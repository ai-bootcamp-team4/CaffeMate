import { createHmac } from 'node:crypto'
import { GoogleAuth } from 'google-auth-library'
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client'
import { MCP_TOOL_NAMES } from './manifest'

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
  if (names.length !== MCP_TOOL_NAMES.length || MCP_TOOL_NAMES.some((name) => !names.includes(name))) {
    throw new Error('MCP_MANIFEST_MISMATCH')
  }
  const result = await client.callTool({
    name: 'resolve_area', arguments: { query: '수원 아주대학교', country_code: 'KR', limit: 5 },
  })
  const structured = result.structuredContent as { status?: string } | undefined
  if (!structured || !['OK', 'PARTIAL', 'NOT_FOUND'].includes(structured.status ?? '')) {
    throw new Error('MCP_RESOLVE_AREA_UNSAFE_OUTCOME')
  }

  const badHeaders = new Headers({ Authorization: authorization })
  badHeaders.set('X-CaffeMate-Scope-Token', 'invalid')
  const badScope = await fetch(`${baseUrl}/mcp`, { method: 'POST', headers: badHeaders, body: '{}' })
  if (badScope.status !== 403) throw new Error(`MCP_BAD_SCOPE_NOT_REJECTED_${badScope.status}`)
  console.log(`MCP_SMOKE_OK tools=${names.length} resolve_area=${structured.status} invalid_scope=403`)
} finally {
  await client.close().catch(() => undefined)
}
