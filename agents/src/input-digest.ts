import { createHash } from 'node:crypto'
import type { AgentTask } from './types'

function canonicalize(value: unknown): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value)
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('AGENT_INPUT_DIGEST_NON_FINITE_NUMBER')
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalize).join(',')}]`
  }
  if (typeof value === 'object') {
    const object = value as Record<string, unknown>
    const entries = Object.keys(object)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(object[key])}`)
    return `{${entries.join(',')}}`
  }
  throw new Error(`AGENT_INPUT_DIGEST_UNSUPPORTED_VALUE: ${typeof value}`)
}

export function agentTaskDigestProjection(task: AgentTask): Record<string, unknown> {
  return {
    schema_version: task.schema_version,
    task_id: task.task_id,
    agent_name: task.agent_name,
    task_type: task.task_type,
    workflow_run_id: task.workflow_run_id,
    stage_run_id: task.stage_run_id,
    venture_project_id: task.venture_project_id,
    head_fence: task.head_fence,
    prompt_version: task.prompt_version,
    input_schema_id: task.input_schema_id,
    output_schema_id: task.output_schema_id,
    input_artifacts: task.input_artifacts,
    runtime_tool_policy: task.runtime_tool_policy,
    tool_manifest_digest: task.tool_manifest_digest,
    available_tool_catalog: task.available_tool_catalog,
    payload: task.payload,
  }
}

export function computeAgentTaskInputDigest(task: AgentTask): string {
  const canonical = canonicalize(agentTaskDigestProjection(task))
  return `sha256:${createHash('sha256').update(canonical, 'utf8').digest('hex')}`
}