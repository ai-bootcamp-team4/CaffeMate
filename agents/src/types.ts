export type AgentName =
  | 'INTENT_INTERPRETER'
  | 'EVIDENCE_RESEARCHER'
  | 'PROPOSAL_AGENT'
  | 'DOCUMENT_ANALYST'
  | 'TYPED_CANDIDATE_AUDITOR'

export type TaskType =
  | 'INTENT_DELTA'
  | 'EVIDENCE_PLAN'
  | 'EVIDENCE_ASSESS'
  | 'PROPOSE_INDEPENDENT'
  | 'PROPOSE_FRANCHISE'
  | 'DOCUMENT_EXTRACT'
  | 'CANDIDATE_AUDIT'

export interface HeadFence {
  workflow_generation: number
  state_version: number
  founder_snapshot_id: string | null
  area_snapshot_id: string | null
  evidence_snapshot_id: string | null
  policy_snapshot_id: string
  index_generation_id: string | null
  seed_registry_id: string | null
}

export interface AgentTask {
  schema_version: '1.0.0'
  task_id: string
  invocation_id: string
  repair_of_invocation_id?: string
  repair_context?: unknown
  agent_name: AgentName
  task_type: TaskType
  workflow_run_id: string
  stage_run_id: string
  transport_attempt: number
  repair_attempt: number
  venture_project_id: string
  head_fence: HeadFence
  prompt_version: string
  input_schema_id: string
  output_schema_id: string
  input_artifacts: unknown[]
  input_digest: string
  deadline_at: string
  runtime_tool_policy: 'NO_DIRECT_TOOL_CALLS'
  tool_manifest_digest: string | null
  available_tool_catalog: unknown[]
  payload: unknown
  trace_context?: unknown
}

export type AgentResultStatus = 'COMPLETE' | 'NEEDS_EVIDENCE' | 'NEEDS_HUMAN' | 'ABSTAIN' | 'INVALID'

export interface AgentTaskResult {
  schema_version: '1.0.0'
  task_id: string
  invocation_id: string
  agent_name: AgentName
  task_type: TaskType
  workflow_run_id: string
  stage_run_id: string
  venture_project_id: string
  head_fence_seen: HeadFence
  input_digest: string
  output_schema_id: string
  status: AgentResultStatus
  payload: unknown | null
  evidence_refs: string[]
  missing_claim_ids: string[]
  reason_codes: string[]
  warnings: string[]
}

export type AgentExecutor = (task: AgentTask) => Promise<AgentTaskResult>
export type AgentExecutorMap = Partial<Record<AgentName, AgentExecutor>>
