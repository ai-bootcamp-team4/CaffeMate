import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/tool-status-matrix.json'
import { MCP_TOOL_NAMES, type McpToolName } from '../src/manifest'
import { validateMcpToolInput, validateMcpToolResult } from '../src/schema-validator'

interface ToolFixtureCase {
  id: string
  tool_name: McpToolName
  status: 'OK' | 'PARTIAL' | 'ERROR'
  input: unknown
  result: { status: string }
}

const cases = fixtureMatrix.cases as ToolFixtureCase[]

describe('MCP 10-tool status fixture matrix', () => {
  it('contains exactly OK, PARTIAL and ERROR for every fixed tool', () => {
    expect(cases).toHaveLength(MCP_TOOL_NAMES.length * 3)
    for (const toolName of MCP_TOOL_NAMES) {
      expect(cases.filter((item) => item.tool_name === toolName).map((item) => item.status).sort()).toEqual([
        'ERROR', 'OK', 'PARTIAL',
      ])
    }
  })

  it('keeps every fixture input and result aligned with the manifest schemas', () => {
    for (const item of cases) {
      expect(validateMcpToolInput(item.tool_name, item.input), `${item.id}:input`).toEqual({ ok: true, errors: [] })
      expect(validateMcpToolResult(item.tool_name, item.result), `${item.id}:result`).toEqual({ ok: true, errors: [] })
      expect(item.result.status, `${item.id}:status`).toBe(item.status)
    }
  })
})
