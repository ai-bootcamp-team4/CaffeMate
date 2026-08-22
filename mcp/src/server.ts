import { AsyncLocalStorage } from 'node:async_hooks'
import { createMcpHandler, fromJsonSchema, McpServer } from '@modelcontextprotocol/server'
import { getMcpToolDefinitions } from './manifest'
import { McpToolRouter, type McpConnectorRegistry, type McpScopeContext } from './router'
import { getToolInputJsonSchema, getToolOutputJsonSchema } from './tool-schemas'

export interface AuthorizedMcpScope {
  ventureProjectId: string
  workflowRunId: string
}

export type McpRequestAuthorizer = (request: Request) => Promise<AuthorizedMcpScope>

export interface CaffeMateMcpServerOptions {
  connectors: McpConnectorRegistry
  authorize: McpRequestAuthorizer
}

export interface CaffeMateMcpHttpHandler {
  fetch(request: Request): Promise<Response>
  close(): Promise<void>
}

export class McpAuthorizationError extends Error {
  constructor(
    public readonly status: 401 | 403,
    public readonly code: string,
    message: string,
  ) {
    super(`${code}: ${message}`)
    this.name = 'McpAuthorizationError'
  }
}

function asStructuredContent(result: unknown): Record<string, unknown> {
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    throw new Error('MCP_STRUCTURED_CONTENT_INVALID: validated tool result must be an object')
  }
  return result as Record<string, unknown>
}

function requestId(value: string | number): string {
  return String(value)
}

interface McpRequestContext {
  scope: AuthorizedMcpScope
  signal: AbortSignal
}

function buildMcpServer(router: McpToolRouter, requestContext: McpRequestContext): McpServer {
  const { scope } = requestContext
  const server = new McpServer(
    { name: 'caffemate-mcp', version: '1.0.0' },
    { capabilities: { tools: {} } },
  )

  for (const definition of getMcpToolDefinitions()) {
    server.registerTool(
      definition.name,
      {
        inputSchema: fromJsonSchema<Record<string, unknown>>(getToolInputJsonSchema(definition)),
        outputSchema: fromJsonSchema<Record<string, unknown>>(getToolOutputJsonSchema(definition)),
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: true,
        },
        _meta: { 'com.caffemate/toolVersion': definition.version },
      },
      async (input, context) => {
        const callScope: McpScopeContext = {
          ventureProjectId: scope.ventureProjectId,
          workflowRunId: scope.workflowRunId,
          requestId: requestId(context.mcpReq.id),
        }
        const result = asStructuredContent(await router.call(definition.name, input, callScope, {
          signal: requestContext.signal,
        }))
        return {
          content: [{ type: 'text', text: JSON.stringify(result) }],
          structuredContent: result,
          _meta: { 'com.caffemate/toolVersion': definition.version },
        }
      },
    )
  }

  return server
}

function authorizationResponse(error: McpAuthorizationError): Response {
  return Response.json(
    {
      jsonrpc: '2.0',
      id: null,
      error: { code: -32001, message: error.code },
    },
    { status: error.status },
  )
}

export function createCaffeMateMcpHttpHandler(options: CaffeMateMcpServerOptions): CaffeMateMcpHttpHandler {
  const router = new McpToolRouter(options.connectors)
  const scopeStorage = new AsyncLocalStorage<McpRequestContext>()
  const handler = createMcpHandler(
    () => {
      const requestContext = scopeStorage.getStore()
      if (!requestContext) throw new Error('MCP_SCOPE_CONTEXT_MISSING')
      return buildMcpServer(router, requestContext)
    },
    { legacy: 'reject' },
  )

  return {
    async fetch(request: Request): Promise<Response> {
      try {
        const scope = await options.authorize(request)
        if (!scope.ventureProjectId || !scope.workflowRunId) {
          throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'authorized scope is incomplete')
        }
        return await scopeStorage.run({ scope, signal: request.signal }, () => handler.fetch(request))
      } catch (error) {
        if (error instanceof McpAuthorizationError) return authorizationResponse(error)
        throw error
      }
    },
    close: () => handler.close(),
  }
}
