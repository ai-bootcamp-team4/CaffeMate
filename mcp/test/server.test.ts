import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MCP_TOOL_NAMES } from '../src/manifest'
import { McpAuthorizationError, createCaffeMateMcpHttpHandler } from '../src/server'

const openHandlers: Array<{ close(): Promise<void> }> = []
const openClients: Client[] = []

afterEach(async () => {
  await Promise.allSettled(openClients.splice(0).map((client) => client.close()))
  await Promise.allSettled(openHandlers.splice(0).map((handler) => handler.close()))
})

function scopedFetch(
  handler: { fetch(request: Request): Promise<Response> },
  scopeToken: string | null = 'scope-ok',
): typeof fetch {
  return (async (url: string | URL | Request, init?: RequestInit) => {
    const headers = new Headers(init?.headers)
    if (scopeToken) headers.set('X-CaffeMate-Scope-Token', scopeToken)
    return handler.fetch(new Request(url, { ...init, headers }))
  }) as typeof fetch
}

function modernClient(handler: { fetch(request: Request): Promise<Response> }, scopeToken: string | null = 'scope-ok') {
  const client = new Client(
    { name: 'caffemate-control-api', version: '1.0.0' },
    { versionNegotiation: { mode: { pin: '2026-07-28' } } },
  )
  const transport = new StreamableHTTPClientTransport(new URL('http://test.local/mcp'), {
    fetch: scopedFetch(handler, scopeToken),
  })
  openClients.push(client)
  return { client, transport }
}

function resolveAreaResult(projectId: string, requestId: string) {
  return {
    schema_version: '1.0.0',
    request_id: requestId,
    tool_name: 'resolve_area',
    tool_version: '1.0.0',
    status: 'OK',
    project_id: projectId,
    evidence_records: [],
    missing_fields: [],
    conflicts: [],
    source_trace: [],
    error_codes: [],
    observed_at: '2026-08-21T09:00:00Z',
    data: [{
      administrative_code: '11200690',
      display_name: '서울 성동구 성수2가3동',
      boundary_version: '2026-01',
      match_kind: 'EXACT',
    }],
  }
}

describe('CaffeMate MCP 2026-07-28 HTTP boundary', () => {
  it('serves exactly the fixed 10 read-only tools to a modern pinned client', async () => {
    const handler = createCaffeMateMcpHttpHandler({
      connectors: {},
      authorize: async (request) => {
        expect(request.headers.get('X-CaffeMate-Scope-Token')).toBe('scope-ok')
        return { ventureProjectId: 'project-1', workflowRunId: 'wf-1' }
      },
    })
    openHandlers.push(handler)
    const { client, transport } = modernClient(handler)

    await client.connect(transport)
    const listed = await client.listTools()

    expect(listed.tools.map((tool) => tool.name)).toEqual(MCP_TOOL_NAMES)
    for (const tool of listed.tools) {
      expect(tool._meta?.['com.caffemate/toolVersion']).toBe('1.0.0')
      expect(tool.annotations?.readOnlyHint).toBe(true)
      expect(tool.annotations?.destructiveHint).toBe(false)
    }
  })

  it('routes tools/call through the validated router and returns structuredContent only as machine data', async () => {
    const connector = vi.fn(async (_input, scope) => resolveAreaResult(scope.ventureProjectId, scope.requestId))
    const handler = createCaffeMateMcpHttpHandler({
      connectors: { resolve_area: connector },
      authorize: async () => ({ ventureProjectId: 'project-1', workflowRunId: 'wf-1' }),
    })
    openHandlers.push(handler)
    const { client, transport } = modernClient(handler)

    await client.connect(transport)
    const result = await client.callTool({
      name: 'resolve_area',
      arguments: { query: '성수동', country_code: 'KR', limit: 5 },
    })

    expect(connector).toHaveBeenCalledTimes(1)
    expect(connector.mock.calls[0]?.[1]).toMatchObject({ ventureProjectId: 'project-1', workflowRunId: 'wf-1' })
    expect(result.structuredContent).toMatchObject({
      tool_name: 'resolve_area',
      project_id: 'project-1',
      status: 'OK',
    })
    expect(result._meta?.['com.caffemate/toolVersion']).toBe('1.0.0')
  })

  it('keeps concurrent request scopes isolated', async () => {
    let releaseFirst: (() => void) | null = null
    const firstStarted = new Promise<void>((resolve) => {
      releaseFirst = resolve
    })
    const connector = vi.fn(async (_input, scope) => {
      if (scope.ventureProjectId === 'project-a') {
        await firstStarted
      } else {
        releaseFirst?.()
      }
      return resolveAreaResult(scope.ventureProjectId, scope.requestId)
    })
    const handler = createCaffeMateMcpHttpHandler({
      connectors: { resolve_area: connector },
      authorize: async (request) => {
        const token = request.headers.get('X-CaffeMate-Scope-Token')
        if (token === 'scope-a') return { ventureProjectId: 'project-a', workflowRunId: 'wf-a' }
        if (token === 'scope-b') return { ventureProjectId: 'project-b', workflowRunId: 'wf-b' }
        throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token is invalid')
      },
    })
    openHandlers.push(handler)
    const first = modernClient(handler, 'scope-a')
    const second = modernClient(handler, 'scope-b')

    await Promise.all([first.client.connect(first.transport), second.client.connect(second.transport)])
    const [firstResult, secondResult] = await Promise.all([
      first.client.callTool({ name: 'resolve_area', arguments: { query: '성수동', country_code: 'KR', limit: 5 } }),
      second.client.callTool({ name: 'resolve_area', arguments: { query: '성수동', country_code: 'KR', limit: 5 } }),
    ])

    expect(firstResult.structuredContent).toMatchObject({ project_id: 'project-a' })
    expect(secondResult.structuredContent).toMatchObject({ project_id: 'project-b' })
  })

  it('rejects requests when the scope authorizer fails', async () => {
    const handler = createCaffeMateMcpHttpHandler({
      connectors: {},
      authorize: async () => {
        throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token is invalid')
      },
    })
    openHandlers.push(handler)
    const { client, transport } = modernClient(handler, null)

    await expect(client.connect(transport)).rejects.toThrow()
  })

  it('rejects a tools/call request when Mcp-Method contradicts the request body', async () => {
    const handler = createCaffeMateMcpHttpHandler({
      connectors: {
        resolve_area: async (_input, scope) => resolveAreaResult(scope.ventureProjectId, scope.requestId),
      },
      authorize: async () => ({ ventureProjectId: 'project-1', workflowRunId: 'wf-1' }),
    })
    openHandlers.push(handler)
    const client = new Client(
      { name: 'caffemate-control-api', version: '1.0.0' },
      { versionNegotiation: { mode: { pin: '2026-07-28' } } },
    )
    openClients.push(client)
    const transport = new StreamableHTTPClientTransport(new URL('http://test.local/mcp'), {
      fetch: (async (url: string | URL | Request, init?: RequestInit) => {
        const headers = new Headers(init?.headers)
        headers.set('X-CaffeMate-Scope-Token', 'scope-ok')
        if (headers.get('Mcp-Method') === 'tools/call') headers.set('Mcp-Method', 'tools/list')
        return handler.fetch(new Request(url, { ...init, headers }))
      }) as typeof fetch,
    })

    await client.connect(transport)
    await expect(client.callTool({
      name: 'resolve_area',
      arguments: { query: '성수동', country_code: 'KR', limit: 5 },
    })).rejects.toThrow()
  })

  it('rejects legacy 2025-era clients instead of silently falling back', async () => {
    const handler = createCaffeMateMcpHttpHandler({
      connectors: {},
      authorize: async () => ({ ventureProjectId: 'project-1', workflowRunId: 'wf-1' }),
    })
    openHandlers.push(handler)
    const client = new Client({ name: 'legacy-client', version: '1.0.0' })
    openClients.push(client)
    const transport = new StreamableHTTPClientTransport(new URL('http://test.local/mcp'), {
      fetch: scopedFetch(handler),
    })

    await expect(client.connect(transport)).rejects.toThrow()
  })
})
