import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
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
    if (!actionSchema?.anyOf) throw new Error('missing projected tool-action union')

    expect(actionSchema.anyOf).toHaveLength(2)
    const branches = Object.fromEntries(actionSchema.anyOf.map((branch) => [
      String(branch.properties?.tool_name.enum?.[0]),
      branch,
    ])) as Record<string, ProjectedSchema>
    expect(Object.keys(branches).sort()).toEqual(['get_area_profile', 'get_source_health'])
    expect(branches.get_source_health?.properties?.typed_arguments).toMatchObject({
      type: 'object',
      additionalProperties: false,
      required: expect.arrayContaining(['source_ids', 'as_of']),
    })
    expect(branches.get_area_profile?.properties?.typed_arguments).toMatchObject({
      type: 'object',
      additionalProperties: false,
      required: expect.arrayContaining(['administrative_code', 'as_of']),
    })
  })
})