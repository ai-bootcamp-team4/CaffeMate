import { computeAgentTaskInputDigest } from './input-digest'
import { TASK_REGISTRY } from './registry'
import { validateAgentTask, validateAgentTaskResult } from './schema-validator'
import { validateAgentSemantics } from './semantic-validator'
import type { AgentExecutorMap, AgentTask, AgentTaskResult, HeadFence } from './types'

export class AgentDispatchError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'AgentDispatchError'
  }
}

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
    throw new AgentDispatchError('RESULT_ECHO_MISMATCH', `result for task ${task.task_id} does not echo the immutable request envelope`)
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
  const resultValidation = validateAgentTaskResult(result)
  if (!resultValidation.ok) {
    throw new AgentDispatchError('RESULT_SCHEMA_INVALID', JSON.stringify(resultValidation.errors))
  }
  assertResultEcho(task, result)
  const semanticValidation = validateAgentSemantics(task, result)
  if (!semanticValidation.ok) {
    throw new AgentDispatchError('RESULT_SEMANTIC_INVALID', JSON.stringify(semanticValidation.issues))
  }
  return result
}
