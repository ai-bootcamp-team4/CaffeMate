import { describe, expect, it, vi } from 'vitest'
import { McpToolError, McpToolRouter } from '../src/router'

const scope = {
  ventureProjectId: 'project-1',
  workflowRunId: 'wf-1',
  requestId: 'request-1',
}

function resolveAreaResult(projectId = 'project-1') {
  return {
    schema_version: '1.0.0',
    request_id: 'request-1',
    tool_name: 'resolve_area',
    tool_version: '1.0.0',
    status: 'OK',
    project_id: projectId,
    evidence_records: [],
    missing_fields: [],
    conflicts: [],
    source_trace: [],
    error_codes: [],
    observed_at: '2026-08-21T08:30:00Z',
    data: [{ administrative_code: '11200690', display_name: '서울 성동구 성수2가3동', boundary_version: '2026-01', match_kind: 'EXACT' }],
  }
}

describe('read-only MCP tool router', () => {
  it('validates input, executes exactly one registered connector, and validates structured output', async () => {
    const connector = vi.fn(async () => resolveAreaResult())
    const router = new McpToolRouter({ resolve_area: connector })

    const result = await router.call('resolve_area', { query: '성수동', country_code: 'KR', limit: 5 }, scope)

    expect(connector).toHaveBeenCalledTimes(1)
    expect(result).toEqual(resolveAreaResult())
  })

  it('fails closed before connector execution when input violates the tool schema', async () => {
    const connector = vi.fn(async () => resolveAreaResult())
    const router = new McpToolRouter({ resolve_area: connector })

    await expect(router.call('resolve_area', { query: '성수동', country_code: 'US', limit: 5 }, scope)).rejects.toMatchObject({ code: 'MCP_INPUT_SCHEMA_INVALID' })
    expect(connector).not.toHaveBeenCalled()
  })

  it('rejects connector output from a different project', async () => {
    const router = new McpToolRouter({ resolve_area: async () => resolveAreaResult('project-2') })

    await expect(router.call('resolve_area', { query: '성수동', country_code: 'KR', limit: 5 }, scope)).rejects.toMatchObject({ code: 'MCP_PROJECT_SCOPE_MISMATCH' })
  })

  it('does not provide undeclared or write-capable tools', async () => {
    const router = new McpToolRouter({})

    await expect(router.call('write_state' as never, {}, scope)).rejects.toBeInstanceOf(McpToolError)
    await expect(router.call('write_state' as never, {}, scope)).rejects.toMatchObject({ code: 'MCP_TOOL_NOT_ALLOWED' })
  })
})
