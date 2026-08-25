import agentRolePayloadsSchema from '../../docs/contracts/agent-role-payloads.schema.json'
import candidateResultSchema from '../../docs/contracts/candidate-result.schema.json'
import commonTypesSchema from '../../docs/contracts/common-types.schema.json'
import evidenceRecordSchema from '../../docs/contracts/evidence-record.schema.json'
import mcpToolContractsSchema from '../../docs/contracts/mcp-tool-contracts.schema.json'
import {
  agentReferencePools,
  proposalSource,
} from './generation-constraints'
import { applyVertexIntentBounds } from './vertex-intent-schema'
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
  RESULT_EXPLAIN: 'resultExplainResult',
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

function setStringEnum(schema: JsonObject, values: readonly string[]): void {
  schema.type = 'string'
  if (values.length > 0) schema.enum = [...values]
  else delete schema.enum
}

// Live Gemini/Vertex rejects otherwise-small schemas when variable controller ID pools are
// repeated as nested enums (verified at EVIDENCE_ASSESS 10x14, Proposal 10x14,
// EVIDENCE_PLAN 10x20, and Document claim pool 100). Keep cardinality bounds here; exact
// reference values remain explicit in generation_constraints and strict Runtime/Control checks.
function boundedStringArray(schema: JsonObject, values: readonly string[]): void {
  schema.maxItems = values.length
  schema.items = { type: 'string' }
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
  if (task.task_type !== 'EVIDENCE_ASSESS') {
    return { claimCount: 0, candidateCount: 0 }
  }
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
  return {
    claimCount: claims.length,
    candidateCount: candidateIds.size,
  }
}

function applyEvidenceAssessBounds(projected: JsonObject, task: AgentTask): void {
  // Production EVIDENCE_ASSESS reaches 10 Claims / 14 candidates. Vertex returns
  // INVALID_ARGUMENT when those dynamic IDs are repeated as nested enums, even
  // though the same compact shape succeeds. Keep exact IDs in generation_constraints
  // and strict Runtime/Control validation; the provider schema only caps assessments.
  const properties = asObject(projected.properties)
  if (!properties) throw new Error('VERTEX_EVIDENCE_ASSESS_SCHEMA_UNRESOLVED')
  const { candidateCount } = evidenceAssessOutputBounds(task)
  const assessments = asObject(properties.assessments)
  if (!assessments) throw new Error('VERTEX_EVIDENCE_ASSESS_BOUNDS_UNRESOLVED')
  assessments.maxItems = candidateCount
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

  proposals.maxItems = 1
  proposalProperties.proposal_id = { type: 'string', enum: [proposalId] }
  proposalProperties.seed_or_brand_id = { type: 'string', enum: [sourceId] }
  proposalProperties.display_name = { type: 'string', enum: [displayName] }

  const evidenceRefs = asObject(proposalProperties.evidence_refs)
  const assumptionRefs = asObject(proposalProperties.assumption_refs)
  const claimRefs = asObject(proposalProperties.claim_refs)
  const fitAssessments = asObject(proposalProperties.fit_assessments)
  const defs = asObject(agentRolePayloadsSchema.$defs)
  const fitAssessmentDefinition = defs ? asObject(defs.candidateFitAssessment) : null
  if (fitAssessments && fitAssessmentDefinition) {
    fitAssessments.items = projectSchema(fitAssessmentDefinition, ROLE_SCHEMA_FILE, 0, new Set())
  }
  const fitAssessment = fitAssessments ? asObject(fitAssessments.items) : null
  const fitProperties = fitAssessment ? asObject(fitAssessment.properties) : null
  if (!evidenceRefs || !assumptionRefs || !claimRefs || !fitAssessments || !fitProperties) {
    throw new Error('VERTEX_PROPOSAL_REFERENCE_SCHEMA_UNRESOLVED')
  }

  const pools = agentReferencePools(task)
  const evidenceIds = pools.evidenceRefs
  const assumptions = pools.assumptionRefs
  const claimIds = pools.claimRefs
  boundedStringArray(evidenceRefs, evidenceIds)
  boundedStringArray(assumptionRefs, assumptions)
  boundedStringArray(claimRefs, claimIds)


  delete fitAssessments.minItems
  fitAssessments.maxItems = 5
  fitProperties.axis = {
    type: 'string',
    enum: [
      'CAPITAL_FIT',
      'OPERATING_FIT',
      'USER_PREFERENCE_FIT',
      'AREA_FIT',
      'EVIDENCE_COMPLETENESS',
    ],
  }
  for (const [propertyName, values] of [
    ['evidence_refs', evidenceIds],
    ['assumption_refs', assumptions],
    ['claim_refs', claimIds],
  ] as const) {
    const referenceSchema = asObject(fitProperties[propertyName])
    if (!referenceSchema) throw new Error(`VERTEX_PROPOSAL_FIT_REFERENCE_SCHEMA_UNRESOLVED: ${propertyName}`)
    boundedStringArray(referenceSchema, values)
  }
}

function applyCandidateAuditBounds(projected: JsonObject, task: AgentTask): void {
  const pools = agentReferencePools(task)
  const properties = asObject(projected.properties)
  const audits = properties ? asObject(properties.candidate_audits) : null
  const audit = audits ? asObject(audits.items) : null
  const auditProperties = audit ? asObject(audit.properties) : null
  const findings = auditProperties ? asObject(auditProperties.findings) : null
  const finding = findings ? asObject(findings.items) : null
  const findingProperties = finding ? asObject(finding.properties) : null
  if (!audits || !auditProperties || !findingProperties) {
    throw new Error('VERTEX_CANDIDATE_AUDIT_SCHEMA_UNRESOLVED')
  }

  audits.maxItems = pools.candidateRefs.length
  const candidateId = asObject(auditProperties.candidate_id)
  if (!candidateId) throw new Error('VERTEX_CANDIDATE_AUDIT_ID_SCHEMA_UNRESOLVED')
  setStringEnum(candidateId, pools.candidateRefs)
  for (const [key, values] of [
    ['claim_refs', pools.claimRefs],
    ['evidence_refs', pools.evidenceRefs],
    ['calculation_refs', pools.calculationRefs],
  ] as const) {
    const schema = asObject(findingProperties[key])
    if (!schema) throw new Error(`VERTEX_CANDIDATE_AUDIT_REFS_UNRESOLVED: ${key}`)
    boundedStringArray(schema, values)
  }
}

function applyResultExplainBounds(projected: JsonObject, task: AgentTask): void {
  const pools = agentReferencePools(task)
  const properties = asObject(projected.properties)
  const evidenceRefs = properties ? asObject(properties.evidence_refs) : null
  if (!evidenceRefs) throw new Error('VERTEX_RESULT_EXPLAIN_SCHEMA_UNRESOLVED')
  boundedStringArray(evidenceRefs, pools.evidenceRefs)
}

export function buildVertexRolePayloadSchema(task: AgentTask): JsonObject {
  const taskType = task.task_type
  const defName = ROLE_PAYLOAD_DEF[taskType]
  const defs = asObject(agentRolePayloadsSchema.$defs)
  const schema = defs ? asObject(defs[defName]) : null
  if (!schema) throw new Error(`VERTEX_ROLE_SCHEMA_UNRESOLVED: ${taskType}`)
  const projected = projectSchema(schema, ROLE_SCHEMA_FILE, 0, new Set())
  if (taskType === 'INTENT_DELTA') applyVertexIntentBounds(projected, task)
  if (taskType === 'EVIDENCE_PLAN') applyEvidencePlanToolActionSchema(projected, task)
  if (taskType === 'EVIDENCE_ASSESS') applyEvidenceAssessBounds(projected, task)
  if (taskType === 'PROPOSE_INDEPENDENT' || taskType === 'PROPOSE_FRANCHISE') applyProposalBounds(projected, task)
  if (taskType === 'CANDIDATE_AUDIT') applyCandidateAuditBounds(projected, task)
  if (taskType === 'RESULT_EXPLAIN') applyResultExplainBounds(projected, task)
  return projected
}
