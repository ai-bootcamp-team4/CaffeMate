import { computeAgentTaskInputDigest } from './input-digest'
import { TASK_REGISTRY } from './registry'
import { validateAgentTask, validateAgentTaskResult } from './schema-validator'
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

type AgentResultValidationOutcome = 'VALID' | 'REJECTED'

function resultDecision(result: AgentTaskResult): string | undefined {
  if (!result.payload || typeof result.payload !== 'object' || Array.isArray(result.payload)) return undefined
  const decision = (result.payload as Record<string, unknown>).decision
  return typeof decision === 'string' ? decision.slice(0, 64) : undefined
}

function resultCollectionCount(result: AgentTaskResult, key: string): number | undefined {
  if (!result.payload || typeof result.payload !== 'object' || Array.isArray(result.payload)) return undefined
  const value = (result.payload as Record<string, unknown>)[key]
  return Array.isArray(value) ? value.length : undefined
}

function recordResultValidation(
  task: AgentTask,
  result: AgentTaskResult,
  outcome: AgentResultValidationOutcome,
  error?: AgentDispatchError,
): void {
  const validatorCodes = error
    ? [...new Set([error.code, ...error.validatorErrors.map((item) => item.code)])].slice(0, 20)
    : []
  console.info(JSON.stringify({
    event: 'AGENT_RESULT_VALIDATION',
    task_type: task.task_type,
    preflight: task.task_id.startsWith('runtime-preflight-'),
    repair_attempt: task.repair_attempt,
    outcome,
    result_status: result.status,
    ...(resultDecision(result) ? { decision: resultDecision(result) } : {}),
    ...(resultCollectionCount(result, 'candidate_proposals') !== undefined
      ? { candidate_proposal_count: resultCollectionCount(result, 'candidate_proposals') }
      : {}),
    ...(resultCollectionCount(result, 'candidate_audits') !== undefined
      ? { candidate_audit_count: resultCollectionCount(result, 'candidate_audits') }
      : {}),
    ...(validatorCodes.length > 0 ? { validator_codes: validatorCodes } : {}),
  }))
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
        code: `RESULT_SCHEMA_${error.keyword.replace(/[^a-z0-9]+/gi, '_').toUpperCase()}`,
        json_pointer: error.path,
        message: error.message.slice(0, 500),
      })),
    )
  }
  assertResultEcho(task, result)
}

export async function dispatchAgentTask(task: AgentTask, executors: AgentExecutorMap): Promise<AgentTaskResult> {
  validateAgentTaskForDispatch(task)

  const registration = TASK_REGISTRY[task.task_type]
  const executor = executors[registration.agentName]
  if (!executor) {
    throw new AgentDispatchError('AGENT_EXECUTOR_UNAVAILABLE', `no executor is registered for ${registration.agentName}`)
  }

  // 사용자 의도: LLM 출력 오류 때문에 같은 요청을 반복 생성하지 않는다.
  // Runtime은 구조만 한 번 검증하고, 제품 의미는 Control API의 단일 경계가 판정한다.
  const result = await executor(task)
  try {
    validateResult(task, result)
    recordResultValidation(task, result, 'VALID')
    return result
  } catch (error) {
    if (error instanceof AgentDispatchError) {
      recordResultValidation(task, result, 'REJECTED', error)
    }
    throw error
  }
}
