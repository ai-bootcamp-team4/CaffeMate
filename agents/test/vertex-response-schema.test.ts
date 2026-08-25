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
  oneOf?: ProjectedSchema[]
  maxItems?: number
  minItems?: number
  minimum?: number
  maximum?: number
}

function variants(schema: ProjectedSchema | undefined): ProjectedSchema[] {
  if (!schema) return []
  return schema.anyOf ?? [schema]
}

function intentTask(): AgentTask {
  const item = fixtureMatrix.cases.find((entry) => entry.task.task_type === 'INTENT_DELTA' && entry.result.status === 'COMPLETE')
  if (!item) throw new Error('missing INTENT_DELTA fixture')
  return structuredClone(item.task) as unknown as AgentTask
}

function evidencePlanTask(): AgentTask {
  const item = fixtureMatrix.cases.find((entry) => entry.task.task_type === 'EVIDENCE_PLAN' && entry.result.status === 'COMPLETE')
  if (!item) throw new Error('missing EVIDENCE_PLAN fixture')
  return structuredClone(item.task) as unknown as AgentTask
}

function evidenceAssessTask(): AgentTask {
  const item = fixtureMatrix.cases.find((entry) => entry.task.task_type === 'EVIDENCE_ASSESS' && entry.result.status === 'COMPLETE')
  if (!item) throw new Error('missing EVIDENCE_ASSESS fixture')
  const task = structuredClone(item.task) as unknown as AgentTask
  const payload = task.payload as Record<string, unknown>
  payload.executed_actions = [{
    structured_result: {
      evidence_records: [
        { evidence_id: 'evidence-1' },
        { evidence_id: 'evidence-2' },
        { evidence_id: 'evidence-2' },
      ],
    },
  }]
  return task
}

function independentProposalTask(): AgentTask {
  const item = fixtureMatrix.cases.find((entry) => entry.task.task_type === 'PROPOSE_INDEPENDENT' && entry.result.status === 'COMPLETE')
  if (!item) throw new Error('missing PROPOSE_INDEPENDENT fixture')
  return structuredClone(item.task) as unknown as AgentTask
}

describe('Vertex role response schema projection', () => {
  it('bounds INTENT_DELTA to controller-provided fields and operation ids', () => {
    const task = intentTask()
    const payload = task.payload as Record<string, unknown>
    payload.allowed_field_paths = ['/founder/borrowing_intent', '/founder/own_funds_krw']
    payload.operation_id_pool = ['op-1', 'op-2', 'op-3']

    const roleSchema = buildVertexRolePayloadSchema(task) as ProjectedSchema
    const operation = roleSchema.properties?.operations.items
    const responseSchema = buildAgentTaskResultResponseJsonSchema(task) as ProjectedSchema
    if (!operation?.properties) throw new Error('missing INTENT_DELTA operation schema')

    expect(roleSchema.properties?.operations.maxItems).toBe(2)
    expect(operation.properties.op_id.enum).toEqual(['op-1', 'op-2', 'op-3'])
    expect(operation.properties.field_path.enum).toEqual([
      '/founder/borrowing_intent',
      '/founder/own_funds_krw',
    ])
    expect(operation.properties.kind.enum).toEqual(['SET'])
    expect(operation.anyOf).toBeUndefined()
    const expectedValues = variants(operation.properties.expected_old_value)
    expect(expectedValues).toEqual(expect.arrayContaining([
      expect.objectContaining({ properties: expect.objectContaining({ value: { enum: ['NO'] } }) }),
      expect.objectContaining({ properties: expect.objectContaining({ value: { enum: [100_000_000] } }) }),
    ]))
    const typedValues = variants(operation.properties.typed_value)
    expect(typedValues).toEqual(expect.arrayContaining([
      expect.objectContaining({ properties: expect.objectContaining({ value: { enum: ['YES', 'NO', 'UNDECIDED'] } }) }),
      expect.objectContaining({ properties: expect.objectContaining({ value: { type: 'integer', minimum: 0 } }) }),
    ]))
    expect(operation.properties.unit.type).toBe('null')
    expect(operation.properties.source_span.properties?.start.maximum).toBe(15)
    expect(operation.properties.source_span.properties?.end.maximum).toBe(16)
    expect(roleSchema.properties?.clarifying_questions.maxItems).toBe(3)
    expect(roleSchema.properties?.affected_workflow_codes.maxItems).toBe(1)
    expect(roleSchema.properties?.affected_workflow_codes.items?.enum).toEqual(['FIRST_PROPOSAL'])
    expect(roleSchema.properties?.risk_flags.maxItems).toBe(5)
    expect(responseSchema.properties?.evidence_refs.maxItems).toBe(0)
    expect(responseSchema.properties?.missing_claim_ids.maxItems).toBe(0)
    expect(responseSchema.properties?.reason_codes.maxItems).toBe(5)
    expect(responseSchema.properties?.warnings.maxItems).toBe(5)
    expect(responseSchema.properties).not.toHaveProperty('task_id')
    expect(responseSchema.properties).not.toHaveProperty('invocation_id')
    expect(responseSchema.properties).not.toHaveProperty('head_fence_seen')
    expect(responseSchema.properties).not.toHaveProperty('input_digest')
    expect(responseSchema.properties).not.toHaveProperty('output_schema_id')
    expect(JSON.stringify(responseSchema).length).toBeLessThan(6_000)
  })

  it('uses only field-valid SET, UNSET, ADD and REMOVE intent branches', () => {
    const task = intentTask()
    const payload = task.payload as Record<string, unknown>
    const projection = payload.current_state_projection as { founder: Record<string, unknown> }
    projection.founder.max_loss_krw = 25_000_000
    projection.founder.preferences = Array.from(
      { length: 8 },
      (_, index) => `${index}${'😀'.repeat(63)}`,
    )
    projection.founder.avoidances = Array.from(
      { length: 8 },
      (_, index) => `${index}${'🫠'.repeat(63)}`,
    )
    projection.founder.target_area_input = '🏙️'.repeat(128)
    payload.allowed_field_paths = [
      '/founder/target_area_input',
      '/founder/own_funds_krw',
      '/founder/borrowing_intent',
      '/founder/cafe_type_preference',
      '/founder/operation_mode',
      '/founder/max_loss_krw',
      '/founder/preferences',
      '/founder/avoidances',
    ]
    payload.operation_id_pool = Array.from({ length: 20 }, (_, index) => `op-${index + 1}`)

    const responseSchema = buildAgentTaskResultResponseJsonSchema(task) as ProjectedSchema
    const operation = responseSchema.properties?.payload.anyOf?.[0]
      ?.properties?.operations.items
    if (!operation?.properties) throw new Error('missing flattened INTENT_DELTA operation schema')
    expect(operation.anyOf).toBeUndefined()
    expect(operation.properties.field_path.enum).toEqual(payload.allowed_field_paths)
    expect(operation.properties.kind.enum).toEqual(['SET', 'UNSET', 'ADD', 'REMOVE'])
    const expectedValues = variants(operation.properties.expected_old_value)
    const typedValues = variants(operation.properties.typed_value)
    expect(expectedValues.some((item) => item.properties?.value.type === 'null')).toBe(true)
    expect(expectedValues.some((item) => item.properties?.value.enum?.[0] === 25_000_000)).toBe(true)
    expect(typedValues.some((item) => item.properties?.value.type === 'null')).toBe(true)
    expect(typedValues.some((item) => item.properties?.value.type === 'integer'
      && item.properties.value.minimum === 0)).toBe(true)
    expect(typedValues.filter((item) => item.properties?.value.enum?.length === 8)).toHaveLength(2)
    expect(new TextEncoder().encode(JSON.stringify(responseSchema)).byteLength).toBeLessThan(20_000)
  })

  it('does not offer a no-op max-loss UNSET branch when State is already null', () => {
    const task = intentTask()
    const payload = task.payload as Record<string, unknown>
    const projection = payload.current_state_projection as { founder: Record<string, unknown> }
    projection.founder.max_loss_krw = null
    payload.allowed_field_paths = ['/founder/max_loss_krw']

    const schema = buildVertexRolePayloadSchema(task) as ProjectedSchema
    const operation = schema.properties?.operations.items
    expect(operation?.anyOf).toBeUndefined()
    expect(operation?.properties?.kind.enum).toEqual(['SET'])
    expect(operation?.properties?.typed_value.properties?.value.type).toBe('integer')
  })

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

  it('keeps the provider schema small while bounding evidence assessment output', () => {
    const task = evidenceAssessTask()
    const payload = task.payload as Record<string, unknown>
    const baseClaim = (payload.claims as Array<Record<string, unknown>>)[0]
    payload.claims = Array.from(
      { length: 10 },
      (_, index) => ({ ...baseClaim, claim_id: `claim-${index + 1}` }),
    )
    payload.executed_actions = [{
      structured_result: {
        evidence_records: Array.from(
          { length: 14 },
          (_, index) => ({ evidence_id: `evidence-${index + 1}-${'x'.repeat(48)}` }),
        ),
      },
    }]
    const roleSchema = buildVertexRolePayloadSchema(task) as ProjectedSchema
    const responseSchema = buildAgentTaskResultResponseJsonSchema(task) as ProjectedSchema

    expect(roleSchema.properties?.assessments.minItems).toBeUndefined()
    expect(roleSchema.properties?.assessments.maxItems).toBe(14)
    expect(roleSchema.properties?.assessments.items?.properties?.candidate_ref.enum).toBeUndefined()
    expect(roleSchema.properties?.assessments.items?.properties?.claim_id.enum).toBeUndefined()
    expect(roleSchema.properties?.missing_claims.maxItems).toBeUndefined()
    expect(roleSchema.properties?.conflict_proposals.maxItems).toBeUndefined()
    expect(responseSchema.properties?.evidence_refs.maxItems).toBe(14)
    expect(responseSchema.properties?.missing_claim_ids.maxItems).toBe(10)
  })

  it('bounds a proposal call to its single allocated source and separates assumptions from Evidence', () => {
    const task = independentProposalTask()
    const roleSchema = buildVertexRolePayloadSchema(task) as ProjectedSchema
    const responseSchema = buildAgentTaskResultResponseJsonSchema(task) as ProjectedSchema
    const proposals = roleSchema.properties?.candidate_proposals
    const proposal = proposals?.items

    expect(proposals?.minItems).toBeUndefined()
    expect(proposals?.maxItems).toBe(1)
    expect(proposal?.properties?.proposal_id.enum).toEqual(['proposal-independent-1'])
    expect(proposal?.properties?.seed_or_brand_id.enum).toEqual(['independent-small-v1'])
    expect(proposal?.properties?.display_name.enum).toEqual(['소형 개인카페 모델'])
    expect(proposal?.properties?.evidence_refs.maxItems).toBe(0)
    expect(proposal?.properties?.assumption_refs.maxItems).toBe(1)
    expect(proposal?.properties?.assumption_refs.items?.type).toBe('string')
    expect(proposal?.properties?.fit_assessments.minItems).toBeUndefined()
    expect(proposal?.properties?.fit_assessments.maxItems).toBe(5)
    expect(proposal?.properties?.fit_assessments.items?.properties?.axis.enum).toEqual([
      'CAPITAL_FIT',
      'OPERATING_FIT',
      'USER_PREFERENCE_FIT',
      'AREA_FIT',
      'EVIDENCE_COMPLETENESS',
    ])
    expect(responseSchema.properties?.evidence_refs.maxItems).toBe(0)
    expect(responseSchema.properties?.missing_claim_ids.maxItems).toBe(0)
  })
})
