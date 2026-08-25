import { describe, expect, it } from 'vitest'
import type { HeadFence, WorkflowRun } from '../apiClient'
import {
  SIMULATION_WORKFLOW_DURATION_MS,
  buildSimulationWorkflowProgress,
  simulationWorkflowStages,
} from './workflow'

const head: HeadFence = {
  workflow_generation: 1,
  state_version: 1,
  founder_snapshot_id: 'founder',
  area_snapshot_id: 'area',
  evidence_snapshot_id: 'evidence',
  policy_snapshot_id: 'policy',
  index_generation_id: 'index',
  seed_registry_id: 'seed',
}

const workflow: WorkflowRun = {
  workflow_run_id: 'simulation-workflow',
  project_id: 'simulation-project',
  workflow_code: 'FIRST_PROPOSAL',
  status: 'RUNNING',
  head,
  created_at: '2026-08-25T06:00:00Z',
  updated_at: '2026-08-25T06:00:00Z',
}

describe('UI simulation workflow timeline', () => {
  it('is intentionally long-running enough to exercise progress UX', () => {
    expect(SIMULATION_WORKFLOW_DURATION_MS).toBeGreaterThanOrEqual(30_000)
    expect(simulationWorkflowStages.length).toBeGreaterThanOrEqual(6)
  })

  it('exposes stage-by-stage progress instead of completing immediately', () => {
    const initial = buildSimulationWorkflowProgress(workflow, 0)
    expect(initial.status).toBe('RUNNING')
    expect(initial.completed_stage_count).toBe(0)
    expect(initial.current_stage_codes).toEqual([simulationWorkflowStages[0].stage_code])
    expect(initial.stages.filter((stage) => stage.status === 'RUNNING')).toHaveLength(1)

    const middle = buildSimulationWorkflowProgress(workflow, Math.floor(SIMULATION_WORKFLOW_DURATION_MS / 2))
    expect(middle.status).toBe('RUNNING')
    expect(middle.completed_stage_count).toBeGreaterThan(0)
    expect(middle.completed_stage_count).toBeLessThan(middle.total_stage_count)
    expect(middle.stages.some((stage) => stage.status === 'SUCCEEDED')).toBe(true)
    expect(middle.stages.some((stage) => stage.status === 'PENDING')).toBe(true)

    const terminal = buildSimulationWorkflowProgress(workflow, SIMULATION_WORKFLOW_DURATION_MS + 1)
    expect(terminal.status).toBe('SUCCEEDED')
    expect(terminal.completed_stage_count).toBe(simulationWorkflowStages.length)
    expect(terminal.current_stage_codes).toEqual([])
    expect(terminal.poll_after_ms).toBeNull()
    expect(terminal.stages.every((stage) => stage.status === 'SUCCEEDED')).toBe(true)
  })
})