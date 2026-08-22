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

function buildEvidencePlanToolActionSchema(allowedTools: readonly string[]): JsonObject {
  const defs = asObject(agentRolePayloadsSchema.$defs)
  const toolAction = defs ? asObject(defs.toolAction) : null
  if (!toolAction) throw new Error('VERTEX_TOOL_ACTION_SCHEMA_UNRESOLVED')

  const properties = asObject(toolAction.properties)
  if (!properties) throw new Error('VERTEX_TOOL_ACTION_PROPERTIES_UNRESOLVED')
  const conditionals = Array.isArray(toolAction.allOf) ? toolAction.allOf : []
  const allowed = new Set(allowedTools)
  const argumentBranches: JsonObject[] = []

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

    argumentBranches.push({
      title: `${toolName} arguments`,
      ...projectSchema(typedArguments, ROLE_SCHEMA_FILE, 0, new Set()),
    })
  }

  if (argumentBranches.length !== allowed.size) {
    throw new Error('VERTEX_EVIDENCE_PLAN_TOOL_SCHEMA_UNRESOLVED')
  }

  const baseInput: JsonObject = { ...toolAction, properties: { ...properties } }
  delete baseInput.allOf
  const projected = projectSchema(baseInput, ROLE_SCHEMA_FILE, 0, new Set())
  const projectedProperties = asObject(projected.properties)
  if (!projectedProperties) throw new Error('VERTEX_TOOL_ACTION_PROJECTION_INVALID')
  projectedProperties.tool_name = { type: 'string', enum: [...allowedTools] }
  projectedProperties.typed_arguments = { anyOf: argumentBranches }
  return projected
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

export function buildVertexRolePayloadSchema(task: AgentTask): JsonObject {
  const taskType = task.task_type
  const defName = ROLE_PAYLOAD_DEF[taskType]
  const defs = asObject(agentRolePayloadsSchema.$defs)
  const schema = defs ? asObject(defs[defName]) : null
  if (!schema) throw new Error(`VERTEX_ROLE_SCHEMA_UNRESOLVED: ${taskType}`)
  const projected = projectSchema(schema, ROLE_SCHEMA_FILE, 0, new Set())
  if (taskType === 'EVIDENCE_PLAN') applyEvidencePlanToolActionSchema(projected, task)
  return projected
}
