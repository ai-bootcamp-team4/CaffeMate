import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { buildAgentTaskResultResponseJsonSchema } from '../src/vertex-model-client'
import {
  buildVertexRolePayloadSchema,
  normalizeVertexEvidencePlanResult,
} from '../src/vertex-response-schema'
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
  it('keeps one compact EVIDENCE_PLAN action shape with allowed tool and argument unions', () => {
    const schema = buildVertexRolePayloadSchema(evidencePlanTask()) as ProjectedSchema
    const actionSchema = schema.properties?.claim_plans.items?.properties?.support_actions.items
    if (!actionSchema?.properties) throw new Error('missing projected tool-action schema')

    expect(actionSchema.properties.tool_name?.enum).toEqual(['get_area_profile', 'get_source_health'])
    expect(actionSchema.anyOf).toBeUndefined()
    const argumentSchemas = actionSchema.properties.typed_arguments?.anyOf
    if (!argumentSchemas) throw new Error('missing typed argument union')
    expect(argumentSchemas).toHaveLength(2)
    expect(argumentSchemas).toEqual(expect.arrayContaining([expect.objectContaining({
      type: 'object',
      additionalProperties: false,
      required: expect.arrayContaining(['source_ids', 'as_of']),
    }), expect.objectContaining({
      type: 'object',
      additionalProperties: false,
      required: expect.arrayContaining(['administrative_code', 'as_of']),
    })]))
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
    expect(actionSchema?.properties?.typed_arguments.anyOf).toHaveLength(8)
    expect(actionSchema?.anyOf).toBeUndefined()
    expect(JSON.stringify(responseSchema).length).toBeLessThan(16_000)
  })

  it('removes only provider-union extra argument keys without inventing missing values', () => {
    const task = evidencePlanTask()
    const result = {
      task_type: 'EVIDENCE_PLAN',
      payload: {
        claim_plans: [{
          support_actions: [{
            tool_name: 'get_area_profile',
            typed_arguments: {
              administrative_code: '11680',
              boundary_version: '2026-01',
              as_of: '2026-08-22',
              metrics: ['store_count'],
            },
          }],
          counter_actions: [{
            tool_name: 'get_source_health',
            typed_arguments: {
              source_ids: 'wrong-type-stays-wrong',
              unexpected: 'drop-me',
            },
          }, {
            tool_name: 'not_allowed',
            typed_arguments: {
              arbitrary: 'leave-unchanged-for-strict-rejection',
            },
          }],
        }],
      },
    }

    normalizeVertexEvidencePlanResult(task, result)

    const plan = result.payload.claim_plans[0]
    expect(plan.support_actions[0]?.typed_arguments).toEqual({
      administrative_code: '11680',
      boundary_version: '2026-01',
      as_of: '2026-08-22',
    })
    expect(plan.counter_actions[0]?.typed_arguments).toEqual({
      source_ids: 'wrong-type-stays-wrong',
    })
    expect(plan.counter_actions[0]?.typed_arguments).not.toHaveProperty('as_of')
    expect(plan.counter_actions[1]?.typed_arguments).toEqual({
      arbitrary: 'leave-unchanged-for-strict-rejection',
    })
  })
})
