import { describe, expect, it, vi } from 'vitest'
import { dispatchAgentTask } from '../src/dispatcher'
import { computeAgentTaskInputDigest } from '../src/input-digest'
import type { AgentTask, AgentTaskResult } from '../src/types'

const head = {
  workflow_generation: 1,
  state_version: 1,
  founder_snapshot_id: null,
  area_snapshot_id: null,
  evidence_snapshot_id: null,
  policy_snapshot_id: 'policy-1',
  index_generation_id: null,
  seed_registry_id: null,
}

function makeIntentTask(): AgentTask {
  const task: AgentTask = {
    schema_version: '1.0.0', task_id: 'task-1', invocation_id: 'inv-1', agent_name: 'INTENT_INTERPRETER',
    task_type: 'INTENT_DELTA', workflow_run_id: 'wf-1', stage_run_id: 'stage-1', transport_attempt: 1,
    repair_attempt: 0, venture_project_id: 'project-1', head_fence: head, prompt_version: 'intent-interpreter.v2',
    input_schema_id: 'caffemate.agent.intent-input.v1', output_schema_id: 'caffemate.agent.intent-result.v1',
    input_artifacts: [], input_digest: `sha256:${'0'.repeat(64)}`, deadline_at: '2026-08-21T08:30:00Z',
    runtime_tool_policy: 'NO_DIRECT_TOOL_CALLS', tool_manifest_digest: null, available_tool_catalog: [],
    payload: {
      current_state_projection: {
        state_version: 1,
        founder: { target_area_input: '성수동', own_funds_krw: 100000000, borrowing_intent: 'NO', cafe_type_preference: 'OPEN_TO_BOTH', operation_mode: 'DIRECT_FULL_TIME', preferences: [], avoidances: [] },
        area: { resolution_status: 'UNRESOLVED', administrative_code: null, display_name: null, boundary_version: null, coverage_profile: 'N0_NATIONWIDE_FACTS', evidence_ids: [], unavailable_fields: [] },
        active_case_id: null, venture_cases: [],
      },
      latest_user_input: '대출은 안 받을게',
      allowed_field_paths: ['/founder/borrowing_intent'], current_candidate_refs: [], operation_id_pool: ['op-1'],
    },
  }
  task.input_digest = computeAgentTaskInputDigest(task)
  return task
}

function makeIntentResult(task: AgentTask): AgentTaskResult {
  return {
    schema_version: '1.0.0', task_id: task.task_id, invocation_id: task.invocation_id,
    agent_name: task.agent_name, task_type: task.task_type, workflow_run_id: task.workflow_run_id,
    stage_run_id: task.stage_run_id, venture_project_id: task.venture_project_id, head_fence_seen: task.head_fence,
    input_digest: task.input_digest, output_schema_id: task.output_schema_id, status: 'COMPLETE',
    payload: { decision: 'NOOP', operations: [], clarifying_questions: [], affected_workflow_codes: [], risk_flags: [] },
    evidence_refs: [], missing_claim_ids: [], reason_codes: [], warnings: [],
  }
}

describe('deterministic dispatcher', () => {
  it('invokes only the registered child and validates the echoed envelope', async () => {
    const task = makeIntentTask()
    const child = vi.fn(async () => makeIntentResult(task))

    const result = await dispatchAgentTask(task, { INTENT_INTERPRETER: child })

    expect(child).toHaveBeenCalledTimes(1)
    expect(result.status).toBe('COMPLETE')
  })

  it('schema-rejects a task whose declared agent does not match the task registry', async () => {
    const task = { ...makeIntentTask(), agent_name: 'PROPOSAL_AGENT' as const }
    const child = vi.fn()

    await expect(dispatchAgentTask(task, { INTENT_INTERPRETER: child })).rejects.toThrow('TASK_SCHEMA_INVALID')
    expect(child).not.toHaveBeenCalled()
  })


  it('schema-validates an unknown task type before registry lookup', async () => {
    const task = { ...makeIntentTask(), task_type: 'UNKNOWN_TASK' } as unknown as AgentTask
    const child = vi.fn()

    await expect(dispatchAgentTask(task, { INTENT_INTERPRETER: child })).rejects.toThrow('TASK_SCHEMA_INVALID')
    expect(child).not.toHaveBeenCalled()
  })

  it('rejects a schema-invalid task before invoking a child', async () => {
    const task = { ...makeIntentTask(), unexpected_field: 'nope' } as unknown as AgentTask
    const child = vi.fn(async () => makeIntentResult(task))

    await expect(dispatchAgentTask(task, { INTENT_INTERPRETER: child })).rejects.toThrow('TASK_SCHEMA_INVALID')
    expect(child).not.toHaveBeenCalled()
  })

  it('rejects a task whose logical input no longer matches its input digest', async () => {
    const task = makeIntentTask()
    task.payload = { ...(task.payload as Record<string, unknown>), latest_user_input: '원래 digest 이후에 바뀐 입력' }
    const child = vi.fn(async () => makeIntentResult(task))

    await expect(dispatchAgentTask(task, { INTENT_INTERPRETER: child })).rejects.toThrow('TASK_INPUT_DIGEST_MISMATCH')
    expect(child).not.toHaveBeenCalled()
  })

  it('rejects a schema-invalid result from the child', async () => {
    const task = makeIntentTask()
    const invalidResult = { ...makeIntentResult(task), payload: { decision: 'NOOP' } } as AgentTaskResult
    const child = vi.fn(async () => invalidResult)

    await expect(dispatchAgentTask(task, { INTENT_INTERPRETER: child })).rejects.toThrow('RESULT_SCHEMA_INVALID')
    expect(child).toHaveBeenCalledTimes(2)
  })

  it('repairs one invalid model result inside the managed invocation', async () => {
    const task = makeIntentTask()
    const invalidResult = { ...makeIntentResult(task), payload: { decision: 'NOOP' } } as AgentTaskResult
    const child = vi.fn()
      .mockResolvedValueOnce(invalidResult)
      .mockImplementationOnce(async (repairTask: AgentTask) => makeIntentResult(repairTask))

    const result = await dispatchAgentTask(task, { INTENT_INTERPRETER: child })

    expect(result.status).toBe('COMPLETE')
    expect(child).toHaveBeenCalledTimes(2)
    const repairTask = child.mock.calls[1]?.[0] as AgentTask
    expect(repairTask.invocation_id).toBe(task.invocation_id)
    expect(repairTask.input_digest).toBe(task.input_digest)
    expect(repairTask.repair_attempt).toBe(1)
    expect(repairTask.repair_of_invocation_id).toBe(task.invocation_id)
    expect(repairTask.repair_context).toMatchObject({
      previous_response_digest: expect.stringMatching(/^sha256:[0-9a-f]{64}$/),
      validator_errors: expect.arrayContaining([
        expect.objectContaining({ code: 'RESULT_SCHEMA_REQUIRED' }),
      ]),
    })
  })

  it('logs only safe validation metadata for invalid and repaired Agent results', async () => {
    const task = makeIntentTask()
    task.task_id = 'runtime-preflight-sensitive-task'
    task.input_digest = computeAgentTaskInputDigest(task)
    const invalidResult = { ...makeIntentResult(task), payload: { decision: 'NOOP' } } as AgentTaskResult
    const child = vi.fn()
      .mockResolvedValueOnce(invalidResult)
      .mockImplementationOnce(async (repairTask: AgentTask) => makeIntentResult(repairTask))
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined)

    try {
      await dispatchAgentTask(task, { INTENT_INTERPRETER: child })

      const events = info.mock.calls.map(([line]) => JSON.parse(String(line)))
      expect(events).toEqual([
        expect.objectContaining({
          event: 'AGENT_RESULT_VALIDATION',
          task_type: 'INTENT_DELTA',
          preflight: true,
          repair_attempt: 0,
          outcome: 'REPAIR_REQUIRED',
          validator_codes: expect.arrayContaining(['RESULT_SCHEMA_REQUIRED']),
        }),
        expect.objectContaining({
          event: 'AGENT_RESULT_VALIDATION',
          task_type: 'INTENT_DELTA',
          preflight: true,
          repair_attempt: 1,
          outcome: 'VALID',
        }),
      ])
      const serialized = JSON.stringify(events)
      expect(serialized).not.toContain(task.task_id)
      expect(serialized).not.toContain(task.venture_project_id)
      expect(serialized).not.toContain('대출은 안 받을게')
    } finally {
      info.mockRestore()
    }
  })

  it('rejects a result whose immutable echo differs from the task', async () => {
    const task = makeIntentTask()
    const child = vi.fn(async () => ({ ...makeIntentResult(task), task_id: 'other-task' }))

    await expect(dispatchAgentTask(task, { INTENT_INTERPRETER: child })).rejects.toThrow('RESULT_ECHO_MISMATCH')
    expect(child).toHaveBeenCalledTimes(2)
  })
})
