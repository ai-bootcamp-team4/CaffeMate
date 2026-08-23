import agentRolePayloadsSchema from '../../docs/contracts/agent-role-payloads.schema.json'
import candidateResultSchema from '../../docs/contracts/candidate-result.schema.json'
import commonTypesSchema from '../../docs/contracts/common-types.schema.json'
import evidenceRecordSchema from '../../docs/contracts/evidence-record.schema.json'
import mcpToolContractsSchema from '../../docs/contracts/mcp-tool-contracts.schema.json'
import type { AgentTask, TaskType } from './types'

type JsonObject = Record<string, unknown>

const ROLE_SCHEMA_FILE = 'agent-role-payloads.schema.json'
const MAX_PROJECTION_DEPTH = 6

const SCHEMAS: Readonly<Record<string, JsonObject>> = Object.freeze({
  [ROLE_SCHEMA_FILE]: agentRolePayloadsSchema as JsonObject,
  'candidate-result.schema.json': candidateResultSchema as JsonObject,
  'common-types.schema.json': commonTypesSchema as JsonObject,
  'evidence-record.schema.json': evidenceRecordSchema as JsonObject,
  'mcp-tool-contracts.schema.json': mcpToolContractsSchema as JsonObject,
})

const ROLE_PAYLOAD_DEF: Readonly<Record<TaskType, string>> = Object.freeze({
  INTENT_DELTA: 'intentResult',
  EVIDENCE_PLAN: 'evidencePlanResult',
  EVIDENCE_ASSESS: 'evidenceAssessResult',
  PROPOSE_INDEPENDENT: 'independentProposalResult',
  PROPOSE_FRANCHISE: 'franchiseProposalResult',
  DOCUMENT_EXTRACT: 'documentExtractResult',
  CANDIDATE_AUDIT: 'candidateAuditResult',
})

const SUPPORTED_KEYS = new Set([
  'type',
  'format',
  'title',
  'description',
  'enum',
  'items',
  'prefixItems',
  'minItems',
  'maxItems',
  'minimum',
  'maximum',
  'anyOf',
  'oneOf',
  'properties',
  'additionalProperties',
  'required',
  'propertyOrdering',
])

function asObject(value: unknown): JsonObject | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as JsonObject
}

function basename(path: string): string {
  return path.split('/').filter(Boolean).at(-1) ?? path
}

function resolvePointer(root: unknown, pointer: string): unknown {
  if (!pointer) return root
  if (!pointer.startsWith('/')) throw new Error(`VERTEX_SCHEMA_REF_INVALID: #${pointer}`)
  return pointer.slice(1).split('/').reduce<unknown>((current, encoded) => {
    const object = asObject(current)
    const key = encoded.replace(/~1/g, '/').replace(/~0/g, '~')
    if (!object || !(key in object)) throw new Error(`VERTEX_SCHEMA_REF_UNRESOLVED: #${pointer}`)
    return object[key]
  }, root)
}

function resolveRef(ref: string, currentFile: string): { schema: JsonObject; file: string; key: string } {
  const [pathPart = '', fragment = ''] = ref.split('#', 2)
  const file = pathPart ? basename(pathPart) : currentFile
  const document = SCHEMAS[file]
  if (!document) throw new Error(`VERTEX_SCHEMA_DOCUMENT_UNRESOLVED: ${file}`)
  const resolved = asObject(resolvePointer(document, fragment))
  if (!resolved) throw new Error(`VERTEX_SCHEMA_REF_INVALID: ${ref}`)
  return { schema: resolved, file, key: `${file}#${fragment}` }
}

function looseSchemaFor(schema: JsonObject): JsonObject {
  const type = schema.type
  if (type === 'array') return { type: 'array', items: {} }
  if (type === 'object' || schema.properties) return { type: 'object', additionalProperties: true }
  if (Array.isArray(type)) {
    return {
      anyOf: type.map((item) => typeof item === 'string' ? { type: item } : {}),
    }
  }
  return typeof type === 'string' ? { type } : {}
}

function mergeObjectSchemas(parts: JsonObject[]): JsonObject {
  const merged: JsonObject = {}
  for (const part of parts) {
    for (const [key, value] of Object.entries(part)) {
      if (key === 'properties') {
        merged.properties = {
          ...(asObject(merged.properties) ?? {}),
          ...(asObject(value) ?? {}),
        }
      } else if (key === 'required' && Array.isArray(value)) {
        merged.required = [...new Set([...(Array.isArray(merged.required) ? merged.required : []), ...value])]
      } else {
        merged[key] = value
      }
    }
  }
  return merged
}

function projectSchema(
  schema: JsonObject,
  currentFile: string,
  depth: number,
  seenRefs: ReadonlySet<string>,
): JsonObject {
  if (depth > MAX_PROJECTION_DEPTH) return looseSchemaFor(schema)

  if (typeof schema.$ref === 'string') {
    const resolved = resolveRef(schema.$ref, currentFile)
    if (seenRefs.has(resolved.key)) return looseSchemaFor(resolved.schema)
    return projectSchema(
      resolved.schema,
      resolved.file,
      depth + 1,
      new Set([...seenRefs, resolved.key]),
    )
  }

  const type = schema.type
  if (Array.isArray(type)) {
    return {
      anyOf: type.map((variant) => projectSchema(
        { ...schema, type: variant },
        currentFile,
        depth + 1,
        seenRefs,
      )),
    }
  }

  const base: JsonObject = {}
  for (const [key, value] of Object.entries(schema)) {
    if (key === 'const') {
      base.enum = [value]
    } else if (key === 'properties' && asObject(value)) {
      base.properties = Object.fromEntries(Object.entries(value as JsonObject).map(([property, propertySchema]) => {
        const object = asObject(propertySchema)
        return [property, object ? projectSchema(object, currentFile, depth + 1, seenRefs) : {}]
      }))
    } else if (key === 'items' && asObject(value)) {
      base.items = projectSchema(value as JsonObject, currentFile, depth + 1, seenRefs)
    } else if ((key === 'anyOf' || key === 'oneOf') && Array.isArray(value)) {
      base[key] = value.map((branch) => {
        const object = asObject(branch)
        return object ? projectSchema(object, currentFile, depth + 1, seenRefs) : {}
      })
    } else if (SUPPORTED_KEYS.has(key)) {
      base[key] = value
    }
  }

  const allOf = Array.isArray(schema.allOf) ? schema.allOf : []
  const structuralBranches = allOf.flatMap((branch): JsonObject[] => {
    const object = asObject(branch)
    if (!object || object.if || object.then || object.else) return []
    return [projectSchema(object, currentFile, depth + 1, seenRefs)]
  })
  return structuralBranches.length ? mergeObjectSchemas([base, ...structuralBranches]) : base
}

function evidencePlanAllowedTools(task: AgentTask): string[] {
  const payload = asObject(task.payload)
  const planningConstraints = payload ? asObject(payload.planning_constraints) : null
  const allowedTools = planningConstraints?.allowed_tools
  if (!Array.isArray(allowedTools) || allowedTools.length === 0 || allowedTools.some((tool) => typeof tool !== 'string')) {
    throw new Error('VERTEX_EVIDENCE_PLAN_ALLOWED_TOOLS_INVALID')
  }
  return [...new Set(allowedTools as string[])]
}

function evidencePlanToolArgumentSchemas(allowedTools: readonly string[]): Map<string, JsonObject> {
  const defs = asObject(agentRolePayloadsSchema.$defs)
  const toolAction = defs ? asObject(defs.toolAction) : null
  if (!toolAction) throw new Error('VERTEX_TOOL_ACTION_SCHEMA_UNRESOLVED')
  const conditionals = Array.isArray(toolAction.allOf) ? toolAction.allOf : []
  const allowed = new Set(allowedTools)
  const typedArgumentSchemas = new Map<string, JsonObject>()

  for (const conditional of conditionals) {
    const object = asObject(conditional)
    const ifSchema = object ? asObject(object.if) : null
    const ifProperties = ifSchema ? asObject(ifSchema.properties) : null
    const toolNameSchema = ifProperties ? asObject(ifProperties.tool_name) : null
    const toolName = typeof toolNameSchema?.const === 'string' ? toolNameSchema.const : null
    if (!toolName || !allowed.has(toolName)) continue

    const thenSchema = object ? asObject(object.then) : null
    const thenProperties = thenSchema ? asObject(thenSchema.properties) : null
    const typedArguments = thenProperties ? asObject(thenProperties.typed_arguments) : null
    if (!typedArguments) throw new Error(`VERTEX_TOOL_ARGUMENT_SCHEMA_UNRESOLVED: ${toolName}`)
    typedArgumentSchemas.set(
      toolName,
      projectSchema(typedArguments, ROLE_SCHEMA_FILE, 0, new Set()),
    )
  }

  if (typedArgumentSchemas.size !== allowed.size) {
    const missing = [...allowed].filter((tool) => !typedArgumentSchemas.has(tool))
    throw new Error(`VERTEX_EVIDENCE_PLAN_TOOL_SCHEMA_UNRESOLVED: ${missing.join(',')}`)
  }

  return typedArgumentSchemas
}

function buildEvidencePlanToolActionSchema(allowedTools: readonly string[]): JsonObject {
  const defs = asObject(agentRolePayloadsSchema.$defs)
  const toolAction = defs ? asObject(defs.toolAction) : null
  if (!toolAction) throw new Error('VERTEX_TOOL_ACTION_SCHEMA_UNRESOLVED')

  const properties = asObject(toolAction.properties)
  if (!properties) throw new Error('VERTEX_TOOL_ACTION_PROPERTIES_UNRESOLVED')
  const typedArgumentSchemas = evidencePlanToolArgumentSchemas(allowedTools)

  const compactProperties: JsonObject = { ...properties }
  compactProperties.tool_name = { type: 'string', enum: [...allowedTools] }
  // Vertex rejects the production 6/8-tool full discriminated action union as
  // too complex. This provider schema is generation guidance only; the checked-in
  // AgentTaskResult contract and semantic validator remain the authority boundary.
  compactProperties.typed_arguments = {
    anyOf: allowedTools.map((tool) => typedArgumentSchemas.get(tool) as JsonObject),
  }
  const compactInput: JsonObject = {
    ...toolAction,
    properties: compactProperties,
  }
  delete compactInput.allOf
  return projectSchema(compactInput, ROLE_SCHEMA_FILE, 0, new Set())
}

function normalizeEvidencePlanAction(
  action: unknown,
  options: {
    allowedTools: ReadonlySet<string>
    typedArgumentSchemas: ReadonlyMap<string, JsonObject>
  },
): void {
  const object = asObject(action)
  if (!object) return
  const toolName = object.tool_name
  const typedArguments = asObject(object.typed_arguments)
  if (typeof toolName !== 'string' || !options.allowedTools.has(toolName) || !typedArguments) return

  const schema = options.typedArgumentSchemas.get(toolName)
  const properties = schema ? asObject(schema.properties) : null
  if (!properties) return
  const allowedKeys = new Set(Object.keys(properties))
  // Gemini can merge keys from sibling anyOf argument schemas. Remove only keys
  // impossible for the selected tool; never add missing fields, coerce values,
  // or normalize unknown/disallowed tools, so strict validation still fails closed.
  object.typed_arguments = Object.fromEntries(
    Object.entries(typedArguments).filter(([key]) => allowedKeys.has(key)),
  )
}

export function normalizeVertexEvidencePlanResult(task: AgentTask, result: unknown): unknown {
  if (task.task_type !== 'EVIDENCE_PLAN') return result
  const output = asObject(result)
  const payload = output ? asObject(output.payload) : null
  const claimPlans = payload?.claim_plans
  if (!Array.isArray(claimPlans)) return result

  const allowedTools = evidencePlanAllowedTools(task)
  const typedArgumentSchemas = evidencePlanToolArgumentSchemas(allowedTools)
  const allowed = new Set(allowedTools)
  for (const rawPlan of claimPlans) {
    const plan = asObject(rawPlan)
    if (!plan) continue
    for (const field of ['support_actions', 'counter_actions']) {
      const actions = plan[field]
      if (!Array.isArray(actions)) continue
      for (const action of actions) {
        normalizeEvidencePlanAction(action, {
          allowedTools: allowed,
          typedArgumentSchemas,
        })
      }
    }
  }
  return result
}

function applyEvidencePlanToolActionSchema(projected: JsonObject, task: AgentTask): void {
  const rootProperties = asObject(projected.properties)
  const claimPlans = rootProperties ? asObject(rootProperties.claim_plans) : null
  const claimPlanItem = claimPlans ? asObject(claimPlans.items) : null
  const claimPlanProperties = claimPlanItem ? asObject(claimPlanItem.properties) : null
  if (!claimPlanProperties) throw new Error('VERTEX_EVIDENCE_PLAN_SCHEMA_UNRESOLVED')

  const actionSchema = buildEvidencePlanToolActionSchema(evidencePlanAllowedTools(task))
  for (const propertyName of ['support_actions', 'counter_actions']) {
    const actions = asObject(claimPlanProperties[propertyName])
    if (!actions) throw new Error(`VERTEX_EVIDENCE_PLAN_ACTIONS_SCHEMA_UNRESOLVED: ${propertyName}`)
    actions.items = actionSchema
  }
}

export function evidenceAssessOutputBounds(task: AgentTask): {
  claimCount: number
  candidateCount: number
} {
  if (task.task_type !== 'EVIDENCE_ASSESS') return { claimCount: 0, candidateCount: 0 }
  const payload = asObject(task.payload)
  const claims = Array.isArray(payload?.claims) ? payload.claims : []
  const actions = Array.isArray(payload?.executed_actions) ? payload.executed_actions : []
  const candidateIds = new Set<string>()
  for (const rawAction of actions) {
    const action = asObject(rawAction)
    const result = action ? asObject(action.structured_result) : null
    const records = Array.isArray(result?.evidence_records) ? result.evidence_records : []
    for (const rawRecord of records) {
      const record = asObject(rawRecord)
      if (typeof record?.evidence_id === 'string') candidateIds.add(record.evidence_id)
    }
  }
  return { claimCount: claims.length, candidateCount: candidateIds.size }
}

function applyEvidenceAssessBounds(projected: JsonObject, task: AgentTask): void {
  const properties = asObject(projected.properties)
  if (!properties) throw new Error('VERTEX_EVIDENCE_ASSESS_SCHEMA_UNRESOLVED')
  const { claimCount, candidateCount } = evidenceAssessOutputBounds(task)
  const assessments = asObject(properties.assessments)
  const missingClaims = asObject(properties.missing_claims)
  const conflicts = asObject(properties.conflict_proposals)
  if (!assessments || !missingClaims || !conflicts) {
    throw new Error('VERTEX_EVIDENCE_ASSESS_BOUNDS_UNRESOLVED')
  }
  assessments.maxItems = candidateCount
  missingClaims.maxItems = claimCount
  conflicts.maxItems = claimCount
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

function applyIntentBounds(projected: JsonObject, task: AgentTask): void {
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

function proposalSource(task: AgentTask): JsonObject {
  const payload = asObject(task.payload)
  const key = task.task_type === 'PROPOSE_INDEPENDENT' ? 'model_seeds' : 'franchise_universe'
  const sources = payload && Array.isArray(payload[key]) ? payload[key] : []
  const source = asObject(sources[0])
  if (!source || sources.length !== 1) throw new Error('VERTEX_PROPOSAL_SOURCE_INVALID')
  return source
}

function proposalEvidenceIds(task: AgentTask): string[] {
  const payload = asObject(task.payload)
  const records = payload && Array.isArray(payload.evidence_records) ? payload.evidence_records : []
  return records
    .map((record) => asObject(record)?.evidence_id)
    .filter((value): value is string => typeof value === 'string')
}

function boundedStringArray(schema: JsonObject, values: readonly string[]): void {
  schema.maxItems = values.length
  schema.items = { type: 'string' }
}

function applyProposalBounds(projected: JsonObject, task: AgentTask): void {
  const properties = asObject(projected.properties)
  const proposals = properties ? asObject(properties.candidate_proposals) : null
  const proposal = proposals ? asObject(proposals.items) : null
  const proposalProperties = proposal ? asObject(proposal.properties) : null
  if (!proposals || !proposalProperties) throw new Error('VERTEX_PROPOSAL_SCHEMA_UNRESOLVED')

  const source = proposalSource(task)
  const proposalId = source.proposal_id
  const sourceId = task.task_type === 'PROPOSE_INDEPENDENT' ? source.model_id : source.brand_id
  const displayName = source.display_name
  if (typeof proposalId !== 'string' || typeof sourceId !== 'string' || typeof displayName !== 'string') {
    throw new Error('VERTEX_PROPOSAL_SOURCE_ID_INVALID')
  }

  proposals.minItems = 1
  proposals.maxItems = 1
  proposalProperties.proposal_id = { type: 'string', enum: [proposalId] }
  proposalProperties.seed_or_brand_id = { type: 'string', enum: [sourceId] }
  proposalProperties.display_name = { type: 'string', enum: [displayName] }

  const evidenceRefs = asObject(proposalProperties.evidence_refs)
  const assumptionRefs = asObject(proposalProperties.assumption_refs)
  const claimRefs = asObject(proposalProperties.claim_refs)
  if (!evidenceRefs || !assumptionRefs || !claimRefs) {
    throw new Error('VERTEX_PROPOSAL_REFERENCE_SCHEMA_UNRESOLVED')
  }
  const evidenceIds = proposalEvidenceIds(task)
  boundedStringArray(evidenceRefs, evidenceIds)

  const assumptions = task.task_type === 'PROPOSE_INDEPENDENT' && Array.isArray(source.support_refs)
    ? source.support_refs.filter((value): value is string => typeof value === 'string')
    : []
  boundedStringArray(assumptionRefs, assumptions)
  const payload = asObject(task.payload)
  const claimIds = payload && Array.isArray(payload.claim_id_pool)
    ? payload.claim_id_pool.filter((value): value is string => typeof value === 'string')
    : []
  boundedStringArray(claimRefs, claimIds)
}

export function buildVertexRolePayloadSchema(task: AgentTask): JsonObject {
  const taskType = task.task_type
  const defName = ROLE_PAYLOAD_DEF[taskType]
  const defs = asObject(agentRolePayloadsSchema.$defs)
  const schema = defs ? asObject(defs[defName]) : null
  if (!schema) throw new Error(`VERTEX_ROLE_SCHEMA_UNRESOLVED: ${taskType}`)
  const projected = projectSchema(schema, ROLE_SCHEMA_FILE, 0, new Set())
  if (taskType === 'INTENT_DELTA') applyIntentBounds(projected, task)
  if (taskType === 'EVIDENCE_PLAN') applyEvidencePlanToolActionSchema(projected, task)
  if (taskType === 'EVIDENCE_ASSESS') applyEvidenceAssessBounds(projected, task)
  if (taskType === 'PROPOSE_INDEPENDENT' || taskType === 'PROPOSE_FRANCHISE') applyProposalBounds(projected, task)
  return projected
}
