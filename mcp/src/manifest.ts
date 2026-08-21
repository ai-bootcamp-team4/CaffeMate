import manifest from '../../docs/contracts/mcp-tool-manifest.json'

export const MCP_TOOL_NAMES = [
  'resolve_area',
  'get_area_profile',
  'search_cafe_observations',
  'search_business_events',
  'list_franchise_universe',
  'get_franchise_disclosure',
  'retrieve_official_documents',
  'retrieve_project_documents',
  'get_official_procedure',
  'get_source_health',
] as const

export type McpToolName = typeof MCP_TOOL_NAMES[number]

export interface McpToolDefinition {
  name: McpToolName
  version: string
  input_schema_id: string
  input_schema_ref: string
  output_schema_id: string
  output_schema_ref: string
}

const allowed = new Set<string>(MCP_TOOL_NAMES)
const definitions = new Map<McpToolName, McpToolDefinition>()
for (const row of manifest.tools) {
  if (!allowed.has(row.name)) throw new Error(`MCP_MANIFEST_UNKNOWN_TOOL: ${row.name}`)
  definitions.set(row.name as McpToolName, row as McpToolDefinition)
}

if (definitions.size !== MCP_TOOL_NAMES.length) {
  throw new Error('MCP_MANIFEST_MISMATCH: checked-in manifest must contain exactly the fixed 10-tool registry')
}

export function getMcpToolDefinition(toolName: string): McpToolDefinition | undefined {
  return definitions.get(toolName as McpToolName)
}

export function getMcpToolDefinitions(): readonly McpToolDefinition[] {
  return MCP_TOOL_NAMES.map((name) => definitions.get(name) as McpToolDefinition)
}
