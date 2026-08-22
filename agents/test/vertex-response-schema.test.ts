import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { buildAgentTaskResultResponseJsonSchema } from '../src/vertex-model-client'
import { buildVertexRolePayloadSchema } from '../src/vertex-response-schema'
import type { AgentTask } from '../src/types'

interface ProjectedSchema {
  type?: string
  additionalProperties?: boolean
  required?: string[]
  enum?: unknown[]
  properties?: Record<string, ProjectedSchema>
  items?: ProjectedSchema
  anyOf?: ProjectedSchema[]
}

function evidencePlanTask(): AgentTask {
  const item = fixtureMatrix.cases.find((entry) => entry.task.task_type === 'EVIDENCE_PLAN' && entry.result.status === 'COMPLETE')
  if (!item) throw new Error('missing EVIDENCE_PLAN fixture')
  return structuredClone(item.task) as unknown as AgentTask
}

describe('Vertex role response schema projection', () => {
  it('turns EVIDENCE_PLAN tool actions into an allowed-tool discriminated union', () => {
    const schema = buildVertexRolePayloadSchema(evidencePlanTask()) as ProjectedSchema
    const actionSchema = schema.properties?.claim_plans.items?.properties?.support_actions.items
    const toolNames = actionSchema?.properties?.tool_name.enum
    const correlatedBranches = actionSchema?.anyOf
    if (!correlatedBranches) throw new Error('missing projected tool action union')

    expect(toolNames).toEqual(['get_area_profile', 'get_source_health'])
    expect(actionSchema?.properties?.typed_arguments).toEqual({ type: 'object' })
    expect(correlatedBranches).toEqual(expect.arrayContaining([
      expect.objectContaining({
        properties: expect.objectContaining({
          tool_name: expect.objectContaining({ enum: ['get_area_profile'] }),
          typed_arguments: expect.objectContaining({ title: 'get_area_profile arguments' }),
        }),
      }),
      expect.objectContaining({
        properties: expect.objectContaining({
          tool_name: expect.objectContaining({ enum: ['get_source_health'] }),
          typed_arguments: expect.objectContaining({ title: 'get_source_health arguments' }),
        }),
      }),
    ]))
  })

  it('keeps the full eight-tool evidence plan below the Vertex schema size boundary', () => {
    const task = evidencePlanTask()
    const toolNames = [
      'get_area_profile',
      'search_cafe_observations',
      'search_business_events',
      'retrieve_official_documents',
      'get_source_health',
      'list_franchise_universe',
      'get_franchise_disclosure',
      'get_official_procedure',
    ]
    const template = task.available_tool_catalog[0]
    if (!template) throw new Error('missing tool catalog fixture')
    task.available_tool_catalog = toolNames.map((toolName, index) => ({
      ...template,
      tool_name: toolName,
      tool_version: `1.0.${index}`,
    }))
    const payload = task.payload as Record<string, unknown>
    payload.planning_constraints = {
      ...(payload.planning_constraints as Record<string, unknown>),
      allowed_tools: toolNames,
    }

    const roleSchema = buildVertexRolePayloadSchema(task) as ProjectedSchema
    const actionSchema = roleSchema.properties?.claim_plans.items
      ?.properties?.support_actions.items
    const responseSchema = buildAgentTaskResultResponseJsonSchema(task)

    expect(actionSchema?.properties?.tool_name.enum).toEqual(toolNames)
    expect(actionSchema?.properties?.typed_arguments).toEqual({ type: 'object' })
    expect(actionSchema?.anyOf).toHaveLength(8)
    expect(JSON.stringify(responseSchema).length).toBeLessThan(16_000)
  })
})
