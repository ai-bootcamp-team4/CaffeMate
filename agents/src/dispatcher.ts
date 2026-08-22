import { createHash } from 'node:crypto'
import { computeAgentTaskInputDigest } from './input-digest'
import { canonicalizeJson } from './input-digest'
import { TASK_REGISTRY } from './registry'
import { validateAgentTask, validateAgentTaskResult } from './schema-validator'
import { validateAgentSemantics } from './semantic-validator'
import type { AgentExecutorMap, AgentTask, AgentTaskResult, HeadFence } from './types'

export class AgentDispatchError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly validatorErrors: Array<{
      code: string
      json_pointer: string
      message: string
    }> = [],
  ) {
    super(`${code}: ${message}`)
    this.name = 'AgentDispatchError'
  }
}

const REPAIRABLE_RESULT_CODES = new Set([
  'RESULT_SCHEMA_INVALID',
  'RESULT_ECHO_MISMATCH',
  'RESULT_SEMANTIC_INVALID',
])

function sameHead(left: HeadFence, right: HeadFence): boolean {
  return left.workflow_generation === right.workflow_generation
    && left.state_version === right.state_version
    && left.founder_snapshot_id === right.founder_snapshot_id
    && left.area_snapshot_id === right.area_snapshot_id
    && left.evidence_snapshot_id === right.evidence_snapshot_id
    && left.policy_snapshot_id === right.policy_snapshot_id
    && left.index_generation_id === right.index_generation_id
    && left.seed_registry_id === right.seed_registry_id
}

function assertTaskRegistry(task: AgentTask): void {
  const registration = TASK_REGISTRY[task.task_type]
  if (!registration) {
    throw new AgentDispatchError('TASK_REGISTRY_MISMATCH', `task type ${String(task.task_type)} is not registered`)
  }
  const matches = task.agent_name === registration.agentName
    && task.prompt_version === registration.promptVersion
    && task.input_schema_id === registration.inputSchemaId
    && task.output_schema_id === registration.outputSchemaId
    && task.runtime_tool_policy === 'NO_DIRECT_TOOL_CALLS'

  if (!matches) {
    throw new AgentDispatchError('TASK_REGISTRY_MISMATCH', `task ${task.task_id} does not match the pinned task registry`)
  }
}

function assertTaskInputDigest(task: AgentTask): void {
  const expected = computeAgentTaskInputDigest(task)
  if (task.input_digest !== expected) {
    throw new AgentDispatchError('TASK_INPUT_DIGEST_MISMATCH', `task ${task.task_id} input digest does not match its logical input`)
  }
}

export function validateAgentTaskForDispatch(task: AgentTask): void {
  const taskValidation = validateAgentTask(task)
  if (!taskValidation.ok) {
    throw new AgentDispatchError('TASK_SCHEMA_INVALID', JSON.stringify(taskValidation.errors))
  }
  assertTaskInputDigest(task)
  assertTaskRegistry(task)
}

function assertResultEcho(task: AgentTask, result: AgentTaskResult): void {
  const matches = result.task_id === task.task_id
    && result.invocation_id === task.invocation_id
    && result.agent_name === task.agent_name
    && result.task_type === task.task_type
    && result.workflow_run_id === task.workflow_run_id
    && result.stage_run_id === task.stage_run_id
    && result.venture_project_id === task.venture_project_id
    && result.input_digest === task.input_digest
    && result.output_schema_id === task.output_schema_id
    && sameHead(result.head_fence_seen, task.head_fence)

  if (!matches) {
    throw new AgentDispatchError(
      'RESULT_ECHO_MISMATCH',
      `result for task ${task.task_id} does not echo the immutable request envelope`,
      [{
        code: 'RESULT_ECHO_MISMATCH',
        json_pointer: '',
        message: 'result does not echo the immutable request envelope',
      }],
    )
  }
}

function validateResult(task: AgentTask, result: AgentTaskResult): void {
  const resultValidation = validateAgentTaskResult(result)
  if (!resultValidation.ok) {
    throw new AgentDispatchError(
      'RESULT_SCHEMA_INVALID',
      JSON.stringify(resultValidation.errors),
      resultValidation.errors.slice(0, 50).map((error) => ({
        code: 'RESULT_SCHEMA_INVALID',
        json_pointer: error.path,
        message: error.message.slice(0, 500),
      })),
    )
  }
  assertResultEcho(task, result)
  const semanticValidation = validateAgentSemantics(task, result)
  if (!semanticValidation.ok) {
    throw new AgentDispatchError(
      'RESULT_SEMANTIC_INVALID',
      JSON.stringify(semanticValidation.issues),
      semanticValidation.issues.slice(0, 50).map((issue) => ({
        code: issue.code,
        json_pointer: issue.path,
        message: issue.message.slice(0, 500),
      })),
    )
  }
}

function buildInlineRepairTask(
  task: AgentTask,
  result: AgentTaskResult,
  error: AgentDispatchError,
): AgentTask {
  const previousResponseText = canonicalizeJson(result).slice(0, 65536)
  return {
    ...task,
    repair_attempt: 1,
    repair_of_invocation_id: task.invocation_id,
    repair_context: {
      previous_response_text: previousResponseText,
      previous_response_digest: `sha256:${createHash('sha256').update(previousResponseText).digest('hex')}`,
      validator_errors: error.validatorErrors.length > 0
        ? error.validatorErrors
        : [{ code: error.code, json_pointer: '', message: error.message.slice(0, 500) }],
    },
  }
}

export async function dispatchAgentTask(task: AgentTask, executors: AgentExecutorMap): Promise<AgentTaskResult> {
  validateAgentTaskForDispatch(task)

  const registration = TASK_REGISTRY[task.task_type]
  const executor = executors[registration.agentName]
  if (!executor) {
    throw new AgentDispatchError('AGENT_EXECUTOR_UNAVAILABLE', `no executor is registered for ${registration.agentName}`)
  }

  const result = await executor(task)
  try {
    validateResult(task, result)
    return result
  } catch (error) {
    if (!(error instanceof AgentDispatchError)
      || !REPAIRABLE_RESULT_CODES.has(error.code)
      || task.repair_attempt !== 0) {
      throw error
    }
    const repairTask = buildInlineRepairTask(task, result, error)
    validateAgentTaskForDispatch(repairTask)
    const repaired = await executor(repairTask)
    validateResult(repairTask, repaired)
    return repaired
  }
}
