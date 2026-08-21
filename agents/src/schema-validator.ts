import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020'
import addFormats from 'ajv-formats'
import agentRolePayloadsSchema from '../../docs/contracts/agent-role-payloads.schema.json'
import agentTaskResultSchema from '../../docs/contracts/agent-task-result.schema.json'
import agentTaskSchema from '../../docs/contracts/agent-task.schema.json'
import candidateResultSchema from '../../docs/contracts/candidate-result.schema.json'
import commonTypesSchema from '../../docs/contracts/common-types.schema.json'
import evidenceRecordSchema from '../../docs/contracts/evidence-record.schema.json'
import mcpToolContractsSchema from '../../docs/contracts/mcp-tool-contracts.schema.json'
import type { AgentTask, AgentTaskResult } from './types'

export interface ContractValidationError {
  path: string
  keyword: string
  message: string
}

export type ContractValidation =
  | { ok: true; errors: [] }
  | { ok: false; errors: ContractValidationError[] }

const ajv = new Ajv2020({
  allErrors: true,
  strict: true,
  validateFormats: true,
})
addFormats(ajv)

for (const schema of [
  commonTypesSchema,
  evidenceRecordSchema,
  candidateResultSchema,
  mcpToolContractsSchema,
  agentRolePayloadsSchema,
  agentTaskSchema,
  agentTaskResultSchema,
]) {
  ajv.addSchema(schema)
}

function requireValidator(schemaId: string): ValidateFunction {
  const validator = ajv.getSchema(schemaId)
  if (!validator) throw new Error(`CONTRACT_SCHEMA_NOT_REGISTERED: ${schemaId}`)
  return validator
}

const taskValidator = requireValidator(agentTaskSchema.$id)
const resultValidator = requireValidator(agentTaskResultSchema.$id)

function normalizeErrors(errors: ErrorObject[] | null | undefined): ContractValidationError[] {
  return (errors ?? []).map((error) => ({
    path: error.instancePath,
    keyword: error.keyword,
    message: error.message ?? 'schema validation failed',
  }))
}

function validate(validator: ValidateFunction, value: unknown): ContractValidation {
  if (validator(value)) return { ok: true, errors: [] }
  return { ok: false, errors: normalizeErrors(validator.errors) }
}

export function validateAgentTask(task: AgentTask): ContractValidation {
  return validate(taskValidator, task)
}

export function validateAgentTaskResult(result: AgentTaskResult): ContractValidation {
  return validate(resultValidator, result)
}
