import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { validateAgentTask, validateAgentTaskResult } from '../src/schema-validator'
import { TASK_REGISTRY } from '../src/registry'
import type { AgentTask, AgentTaskResult, TaskType } from '../src/types'

describe('agent contract fixtures', () => {
  it('contains one complete and one abstain fixture for every task type', () => {
    const byTask = new Map<TaskType, Set<string>>()
    for (const fixture of fixtureMatrix.cases) {
      const taskType = fixture.task.task_type as TaskType
      const statuses = byTask.get(taskType) ?? new Set<string>()
      statuses.add(fixture.result.status)
      byTask.set(taskType, statuses)
    }

    expect([...byTask.keys()].sort()).toEqual(Object.keys(TASK_REGISTRY).sort())
    for (const statuses of byTask.values()) {
      expect(statuses.has('COMPLETE')).toBe(true)
      expect(statuses.has('ABSTAIN')).toBe(true)
    }
  })

  it('validates every checked-in task and result against the canonical JSON Schemas', () => {
    for (const fixture of fixtureMatrix.cases) {
      expect(validateAgentTask(fixture.task as AgentTask), fixture.id).toEqual({ ok: true, errors: [] })
      expect(validateAgentTaskResult(fixture.result as AgentTaskResult), fixture.id).toEqual({ ok: true, errors: [] })
    }
  })

  it('fails closed on an invalid task payload', () => {
    const fixture = structuredClone(fixtureMatrix.cases[0])
    delete (fixture.task.payload as Record<string, unknown>).latest_user_input

    const validation = validateAgentTask(fixture.task as AgentTask)
    expect(validation.ok).toBe(false)
    expect(validation.errors.length).toBeGreaterThan(0)
  })
})
