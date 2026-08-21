import { getMcpToolDefinition, type McpToolName } from './manifest'
import { validateMcpToolInput, validateMcpToolResult } from './schema-validator'

export interface McpScopeContext {
  ventureProjectId: string
  workflowRunId: string
  requestId: string
}

export type McpConnector = (input: unknown, scope: McpScopeContext) => Promise<unknown>
export type McpConnectorRegistry = Partial<Record<McpToolName, McpConnector>>

export class McpToolError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'McpToolError'
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export class McpToolRouter {
  constructor(private readonly connectors: McpConnectorRegistry) {}

  async call(toolName: McpToolName, input: unknown, scope: McpScopeContext): Promise<unknown> {
    const definition = getMcpToolDefinition(toolName)
    if (!definition) {
      throw new McpToolError('MCP_TOOL_NOT_ALLOWED', `tool ${String(toolName)} is not in the fixed read-only manifest`)
    }

    const inputValidation = validateMcpToolInput(definition.name, input)
    if (!inputValidation.ok) {
      throw new McpToolError('MCP_INPUT_SCHEMA_INVALID', JSON.stringify(inputValidation.errors))
    }

    const connector = this.connectors[definition.name]
    if (!connector) {
      throw new McpToolError('MCP_CONNECTOR_UNAVAILABLE', `connector ${definition.name} is not configured`)
    }

    const result = await connector(input, scope)
    const resultValidation = validateMcpToolResult(definition.name, result)
    if (!resultValidation.ok) {
      throw new McpToolError('MCP_OUTPUT_SCHEMA_INVALID', JSON.stringify(resultValidation.errors))
    }

    const resultObject = record(result)
    if (!resultObject || resultObject.project_id !== scope.ventureProjectId) {
      throw new McpToolError('MCP_PROJECT_SCOPE_MISMATCH', 'connector output project_id differs from the validated request scope')
    }
    if (resultObject.request_id !== scope.requestId) {
      throw new McpToolError('MCP_REQUEST_ID_MISMATCH', 'connector output request_id differs from the request scope')
    }

    return result
  }
}
