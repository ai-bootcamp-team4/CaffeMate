import { buildSystemInstruction, PROMPTS, type RolePromptVersion } from './prompts'
import { AGENT_MODEL, TASK_REGISTRY, type AgentThinkingLevel } from './registry'
import type {
  AgentExecutor,
  AgentExecutorMap,
  AgentName,
  AgentSemanticResult,
  AgentTask,
  AgentTaskResult,
} from './types'

export interface AgentModelInvocation {
  model: string
  region: typeof AGENT_MODEL.region
  thinkingLevel: AgentThinkingLevel
  maxOutputTokens: number
  agentName: AgentName
  taskType: AgentTask['task_type']
  outputSchemaId: string
  repairAttempt: number
  systemInstruction: string
  task: AgentTask
}

export type AgentModelResponse =
  | { kind: 'TEXT'; text: string }
  | { kind: 'SAFETY_BLOCKED' }

export interface AgentModelClient {
  generate(invocation: AgentModelInvocation): Promise<AgentModelResponse>
}

export interface ApprovedAgentModelConfig {
  id: string
  region: typeof AGENT_MODEL.region
  thinkingLevel: typeof AGENT_MODEL.thinkingLevel
}

export class AgentModelError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'AgentModelError'
  }
}

function registrationFor(task: AgentTask) {
  const registration = TASK_REGISTRY[task.task_type]
  if (!registration) throw new AgentModelError('TASK_REGISTRY_MISMATCH', `task type ${String(task.task_type)} is not registered`)
  return registration
}

export function buildModelInvocation(
  task: AgentTask,
  approvedModel?: ApprovedAgentModelConfig,
): AgentModelInvocation {
  if (!approvedModel) {
    throw new AgentModelError(
      'MODEL_NOT_APPROVED',
      'an approved model id is required after the regional GCP preflight',
    )
  }
  const registration = registrationFor(task)
  let systemInstruction = buildSystemInstruction(registration.promptVersion as RolePromptVersion)
  if (task.repair_attempt === 1) systemInstruction = `${systemInstruction}\n\n${PROMPTS['repair.v1']}`

  return {
    model: approvedModel.id,
    region: approvedModel.region,
    thinkingLevel: registration.thinkingLevel,
    maxOutputTokens: registration.maxOutputTokens,
    agentName: registration.agentName,
    taskType: task.task_type,
    outputSchemaId: registration.outputSchemaId,
    repairAttempt: task.repair_attempt,
    systemInstruction,
    task,
  }
}

const SEMANTIC_RESULT_KEYS = new Set([
  'status',
  'payload',
  'evidence_refs',
  'missing_claim_ids',
  'reason_codes',
  'warnings',
])

function parseJsonResult(text: string): AgentSemanticResult {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new AgentModelError('MODEL_JSON_INVALID', 'model response must be exactly one JSON object')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new AgentModelError('MODEL_JSON_INVALID', 'model response must be exactly one JSON object')
  }
  const unexpected = Object.keys(parsed).filter((key) => !SEMANTIC_RESULT_KEYS.has(key))
  if (unexpected.length > 0) {
    throw new AgentModelError(
      'MODEL_SEMANTIC_ENVELOPE_INVALID',
      `model response contains Runtime-owned fields: ${unexpected.slice(0, 10).join(',')}`,
    )
  }
  return parsed as AgentSemanticResult
}

export function hydrateAgentTaskResult(
  task: AgentTask,
  semantic: AgentSemanticResult,
): AgentTaskResult {
  return {
    schema_version: task.schema_version,
    task_id: task.task_id,
    invocation_id: task.invocation_id,
    agent_name: task.agent_name,
    task_type: task.task_type,
    workflow_run_id: task.workflow_run_id,
    stage_run_id: task.stage_run_id,
    venture_project_id: task.venture_project_id,
    head_fence_seen: structuredClone(task.head_fence),
    input_digest: task.input_digest,
    output_schema_id: task.output_schema_id,
    status: semantic.status,
    payload: semantic.payload,
    evidence_refs: semantic.evidence_refs,
    missing_claim_ids: semantic.missing_claim_ids,
    reason_codes: semantic.reason_codes,
    warnings: semantic.warnings,
  }
}

async function executeModelTask(
  client: AgentModelClient,
  approvedModel: ApprovedAgentModelConfig,
  expectedAgent: AgentName,
  task: AgentTask,
): Promise<AgentTaskResult> {
  if (task.agent_name !== expectedAgent) {
    throw new AgentModelError('MODEL_AGENT_MISMATCH', `executor ${expectedAgent} cannot run task for ${task.agent_name}`)
  }
  const response = await client.generate(buildModelInvocation(task, approvedModel))
  if (response.kind === 'SAFETY_BLOCKED') {
    throw new AgentModelError('SAFETY_BLOCKED', 'model invocation was blocked by the provider safety layer')
  }
  return hydrateAgentTaskResult(task, parseJsonResult(response.text))
}

function executorFor(
  client: AgentModelClient,
  approvedModel: ApprovedAgentModelConfig,
  agentName: AgentName,
): AgentExecutor {
  return (task) => executeModelTask(client, approvedModel, agentName, task)
}

export function createModelExecutors(
  client: AgentModelClient,
  approvedModel: ApprovedAgentModelConfig,
): AgentExecutorMap {
  return {
    INTENT_INTERPRETER: executorFor(client, approvedModel, 'INTENT_INTERPRETER'),
    EVIDENCE_RESEARCHER: executorFor(client, approvedModel, 'EVIDENCE_RESEARCHER'),
    PROPOSAL_AGENT: executorFor(client, approvedModel, 'PROPOSAL_AGENT'),
    DOCUMENT_ANALYST: executorFor(client, approvedModel, 'DOCUMENT_ANALYST'),
    TYPED_CANDIDATE_AUDITOR: executorFor(client, approvedModel, 'TYPED_CANDIDATE_AUDITOR'),
    RESULT_EXPLAINER: executorFor(client, approvedModel, 'RESULT_EXPLAINER'),
  }
}
