import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import commonTypesSchema from '../../docs/contracts/common-types.schema.json'
import evidenceRecordSchema from '../../docs/contracts/evidence-record.schema.json'
import mcpToolContractsSchema from '../../docs/contracts/mcp-tool-contracts.schema.json'
import { getMcpToolDefinitions, type McpToolName } from './manifest'

export interface McpSchemaValidation {
  ok: boolean
  errors: Array<{ path: string; keyword: string; message: string }>
}

const ajv = new Ajv2020({ allErrors: true, strict: true, validateFormats: true })
addFormats(ajv)
ajv.addSchema(commonTypesSchema)
ajv.addSchema(evidenceRecordSchema)
ajv.addSchema(mcpToolContractsSchema)

function resolvedRef(relativeRef: string): string {
  const fragment = relativeRef.split('#')[1]
  if (!fragment) throw new Error(`MCP_SCHEMA_REF_INVALID: ${relativeRef}`)
  return `${mcpToolContractsSchema.$id}#${fragment}`
}

const inputValidators = new Map<McpToolName, ValidateFunction>()
const outputValidators = new Map<McpToolName, ValidateFunction>()
for (const tool of getMcpToolDefinitions()) {
  inputValidators.set(tool.name, ajv.compile({ $ref: resolvedRef(tool.input_schema_ref) }))
  outputValidators.set(tool.name, ajv.compile({ $ref: resolvedRef(tool.output_schema_ref) }))
}

function normalize(errors: ErrorObject[] | null | undefined) {
  return (errors ?? []).map((error) => ({
    path: error.instancePath,
    keyword: error.keyword,
    message: error.message ?? 'schema validation failed',
  }))
}

function validate(validator: ValidateFunction | undefined, value: unknown): McpSchemaValidation {
  if (!validator) return { ok: false, errors: [{ path: '', keyword: 'registry', message: 'tool validator is not registered' }] }
  const ok = validator(value)
  return ok ? { ok: true, errors: [] } : { ok: false, errors: normalize(validator.errors) }
}

export function validateMcpToolInput(toolName: McpToolName, input: unknown): McpSchemaValidation {
  return validate(inputValidators.get(toolName), input)
}

export function validateMcpToolResult(toolName: McpToolName, result: unknown): McpSchemaValidation {
  return validate(outputValidators.get(toolName), result)
}
