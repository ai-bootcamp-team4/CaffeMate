import type { AgentTask } from './types'

type JsonObject = Record<string, unknown>

function asObject(value: unknown): JsonObject | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as JsonObject
}
const INTENT_ENUM_VALUES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  '/founder/borrowing_intent': ['YES', 'NO', 'UNDECIDED'],
  '/founder/cafe_type_preference': ['OPEN_TO_BOTH', 'INDEPENDENT_ONLY', 'FRANCHISE_ONLY'],
  '/founder/operation_mode': ['DIRECT_FULL_TIME', 'DIRECT_PART_TIME', 'EMPLOYEE_LED', 'UNDECIDED'],
})

const INTENT_INTEGER_FIELDS = new Set([
  '/founder/own_funds_krw',
  '/founder/max_loss_krw',
])

const INTENT_COLLECTION_FIELDS = new Set([
  '/founder/preferences',
  '/founder/avoidances',
])

function intentPool(task: AgentTask, field: 'allowed_field_paths' | 'operation_id_pool'): string[] {
  const payload = asObject(task.payload)
  const values = payload?.[field]
  if (!Array.isArray(values) || values.length === 0 || values.some((value) => typeof value !== 'string')) {
    throw new Error(`VERTEX_INTENT_${field.toUpperCase()}_INVALID`)
  }
  return [...new Set(values as string[])]
}

function intentTypedValueSchema(kind: 'NULL' | 'STRING' | 'INTEGER', valueSchema: JsonObject): JsonObject {
  return {
    type: 'object',
    additionalProperties: false,
    required: ['kind', 'value'],
    properties: {
      kind: { enum: [kind] },
      value: valueSchema,
    },
  }
}

function exactIntentTypedValue(value: unknown): JsonObject {
  if (value === null) return intentTypedValueSchema('NULL', { type: 'null' })
  if (typeof value === 'string') return intentTypedValueSchema('STRING', { enum: [value] })
  if (typeof value === 'number' && Number.isInteger(value)) {
    return intentTypedValueSchema('INTEGER', { enum: [value] })
  }
  throw new Error('VERTEX_INTENT_STATE_VALUE_UNSUPPORTED')
}

function intentOperationBranch(
  fieldPath: string,
  kind: 'SET' | 'UNSET' | 'ADD' | 'REMOVE',
  expectedOldValue: JsonObject,
  typedValue: JsonObject,
): JsonObject {
  return {
    type: 'object',
    properties: {
      field_path: { enum: [fieldPath] },
      kind: { enum: [kind] },
      expected_old_value: expectedOldValue,
      typed_value: typedValue,
      unit: { type: 'null' },
    },
  }
}

function intentOperationBranches(task: AgentTask, fieldPaths: readonly string[]): JsonObject[] {
  const payload = asObject(task.payload)
  const state = payload ? asObject(payload.current_state_projection) : null
  const founder = state ? asObject(state.founder) : null
  if (!founder) throw new Error('VERTEX_INTENT_STATE_PROJECTION_INVALID')

  const branches: JsonObject[] = []
  for (const fieldPath of fieldPaths) {
    const fieldName = fieldPath.split('/').at(-1)
    if (!fieldName || !(fieldName in founder)) {
      throw new Error(`VERTEX_INTENT_STATE_FIELD_MISSING: ${fieldPath}`)
    }
    const currentValue = founder[fieldName]

    if (INTENT_COLLECTION_FIELDS.has(fieldPath)) {
      if (!Array.isArray(currentValue) || currentValue.some((value) => typeof value !== 'string')) {
        throw new Error(`VERTEX_INTENT_COLLECTION_STATE_INVALID: ${fieldPath}`)
      }
      branches.push(intentOperationBranch(
        fieldPath,
        'ADD',
        exactIntentTypedValue(null),
        // responseJsonSchema does not support minLength/pattern. The strict
        // semantic validator remains authoritative for non-blank free text.
        intentTypedValueSchema('STRING', { type: 'string' }),
      ))
      const removableItems = [...new Set(currentValue as string[])]
      if (removableItems.length > 0) {
        const removableValue = intentTypedValueSchema('STRING', { enum: removableItems })
        branches.push(intentOperationBranch(
          fieldPath,
          'REMOVE',
          removableValue,
          removableValue,
        ))
      }
      continue
    }

    const expected = exactIntentTypedValue(currentValue)
    if (fieldPath === '/founder/max_loss_krw') {
      branches.push(intentOperationBranch(
        fieldPath,
        'SET',
        expected,
        intentTypedValueSchema('INTEGER', { type: 'integer', minimum: 0 }),
      ))
      if (currentValue !== null) {
        branches.push(intentOperationBranch(
          fieldPath,
          'UNSET',
          expected,
          exactIntentTypedValue(null),
        ))
      }
      continue
    }

    if (INTENT_INTEGER_FIELDS.has(fieldPath)) {
      branches.push(intentOperationBranch(
        fieldPath,
        'SET',
        expected,
        intentTypedValueSchema('INTEGER', { type: 'integer', minimum: 0 }),
      ))
      continue
    }

    const allowedValues = INTENT_ENUM_VALUES[fieldPath]
    branches.push(intentOperationBranch(
      fieldPath,
      'SET',
      expected,
      intentTypedValueSchema('STRING', allowedValues ? { enum: [...allowedValues] } : { type: 'string' }),
    ))
  }
  return branches
}

function distinctIntentPropertySchemas(branches: readonly JsonObject[], property: string): JsonObject[] {
  const schemas = new Map<string, JsonObject>()
  for (const branch of branches) {
    const properties = asObject(branch.properties)
    const schema = properties ? asObject(properties[property]) : null
    if (schema) schemas.set(JSON.stringify(schema), schema)
  }
  return [...schemas.values()]
}

function compactIntentPropertyUnion(schemas: readonly JsonObject[]): JsonObject {
  if (schemas.length === 0) throw new Error('VERTEX_INTENT_PROPERTY_SCHEMA_UNRESOLVED')
  return schemas.length === 1 ? schemas[0] as JsonObject : { anyOf: schemas }
}

export function applyVertexIntentBounds(projected: JsonObject, task: AgentTask): void {
  const taskPayload = asObject(task.payload)
  const properties = asObject(projected.properties)
  const operations = properties ? asObject(properties.operations) : null
  const operation = operations ? asObject(operations.items) : null
  const operationProperties = operation ? asObject(operation.properties) : null
  const latestUserInput = taskPayload?.latest_user_input
  if (!properties || !operations || !operation || !operationProperties
    || typeof latestUserInput !== 'string' || latestUserInput.length === 0) {
    throw new Error('VERTEX_INTENT_SCHEMA_UNRESOLVED')
  }

  const fieldPaths = intentPool(task, 'allowed_field_paths')
  const operationIds = intentPool(task, 'operation_id_pool')
  const branches = intentOperationBranches(task, fieldPaths)
  const kinds = distinctIntentPropertySchemas(branches, 'kind')
    .flatMap((schema) => Array.isArray(schema.enum) ? schema.enum : [])
    .filter((value): value is string => typeof value === 'string')
  operations.maxItems = Math.min(fieldPaths.length, operationIds.length)
  operationProperties.op_id = { type: 'string', enum: operationIds }
  // Vertex may treat a nested operation-level anyOf as guidance and generate
  // values that violate every branch. Put the bounded types directly on each
  // property so structured generation must at least choose a supplied field,
  // operation kind and typed-value shape. The semantic validator remains the
  // authority for the field/kind/value pairing.
  operationProperties.field_path = { type: 'string', enum: fieldPaths }
  operationProperties.kind = { type: 'string', enum: [...new Set(kinds)] }
  operationProperties.expected_old_value = compactIntentPropertyUnion(
    distinctIntentPropertySchemas(branches, 'expected_old_value'),
  )
  operationProperties.typed_value = compactIntentPropertyUnion(
    distinctIntentPropertySchemas(branches, 'typed_value'),
  )
  operationProperties.unit = { type: 'null' }
  delete operation.anyOf

  const sourceSpan = asObject(operationProperties.source_span)
  const sourceSpanProperties = sourceSpan ? asObject(sourceSpan.properties) : null
  const spanStart = sourceSpanProperties ? asObject(sourceSpanProperties.start) : null
  const spanEnd = sourceSpanProperties ? asObject(sourceSpanProperties.end) : null
  if (!spanStart || !spanEnd) throw new Error('VERTEX_INTENT_SOURCE_SPAN_SCHEMA_UNRESOLVED')
  const inputLength = [...latestUserInput].length
  spanStart.maximum = inputLength - 1
  spanEnd.minimum = 1
  spanEnd.maximum = inputLength

  const ambiguityCodes = asObject(operationProperties.ambiguity_codes)
  const clarifyingQuestions = asObject(properties.clarifying_questions)
  const affectedWorkflowCodes = asObject(properties.affected_workflow_codes)
  const riskFlags = asObject(properties.risk_flags)
  if (!ambiguityCodes || !clarifyingQuestions || !affectedWorkflowCodes || !riskFlags) {
    throw new Error('VERTEX_INTENT_ARRAY_SCHEMA_UNRESOLVED')
  }
  ambiguityCodes.maxItems = 3
  clarifyingQuestions.maxItems = 3
  affectedWorkflowCodes.maxItems = 1
  affectedWorkflowCodes.items = { type: 'string', enum: ['FIRST_PROPOSAL'] }
  riskFlags.maxItems = 5
}
