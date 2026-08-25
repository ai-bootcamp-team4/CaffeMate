import type { HeadFence, WorkflowProgress, WorkflowRun, WorkflowStageProgress } from '../apiClient'

export interface SimulationWorkflowStage {
  stage_code: string
  duration_ms: number
}

export const simulationWorkflowStages: readonly SimulationWorkflowStage[] = [
  { stage_code: 'AREA_CONTEXT', duration_ms: 4_500 },
  { stage_code: 'EVIDENCE_RETRIEVAL', duration_ms: 8_000 },
  { stage_code: 'EVIDENCE_ASSESS', duration_ms: 6_500 },
  { stage_code: 'PROPOSAL_GENERATION', duration_ms: 7_000 },
  { stage_code: 'FINANCE_AND_RANK', duration_ms: 5_500 },
  { stage_code: 'CANDIDATE_AUDIT', duration_ms: 4_500 },
  { stage_code: 'COMMIT_RESULT', duration_ms: 2_500 },
] as const

export const feedbackRecalculationStages: readonly SimulationWorkflowStage[] = [
  { stage_code: 'PROPOSAL_GENERATION', duration_ms: 4_000 },
  { stage_code: 'FINANCE_AND_RANK', duration_ms: 4_500 },
  { stage_code: 'CANDIDATE_AUDIT', duration_ms: 2_500 },
  { stage_code: 'COMMIT_RESULT', duration_ms: 1_500 },
] as const

export const financialRecalculationStages: readonly SimulationWorkflowStage[] = [
  { stage_code: 'FINANCE_AND_RANK', duration_ms: 4_500 },
  { stage_code: 'CANDIDATE_AUDIT', duration_ms: 2_500 },
  { stage_code: 'COMMIT_RESULT', duration_ms: 1_500 },
] as const

export const SIMULATION_WORKFLOW_DURATION_MS = simulationWorkflowStages
  .reduce((sum, stage) => sum + stage.duration_ms, 0)

export function createSimulationWorkflowRun(
  workflowRunId: string,
  projectId: string,
  head: HeadFence,
  startedAtMs = Date.now(),
): WorkflowRun {
  const timestamp = new Date(startedAtMs).toISOString()
  return {
    workflow_run_id: workflowRunId,
    project_id: projectId,
    workflow_code: 'FIRST_PROPOSAL',
    status: 'RUNNING',
    head,
    created_at: timestamp,
    updated_at: timestamp,
  }
}

export function createSimulationWorkflowRegistry(projectId: string, timeScaleInput = 1) {
  const timeScale = Math.max(0.001, timeScaleInput)
  const runs = new Map<string, WorkflowRun>()
  const stagesByRun = new Map<string, readonly SimulationWorkflowStage[]>()
  const startedAt = new Map<string, number>()
  const forcedCompleted = new Set<string>()
  let activeWorkflowRunId: string | null = null

  return {
    start(
      workflowRunId: string,
      head: HeadFence,
      stages: readonly SimulationWorkflowStage[] = simulationWorkflowStages,
    ) {
      const startedAtMs = Date.now()
      const run = createSimulationWorkflowRun(workflowRunId, projectId, head, startedAtMs)
      runs.set(workflowRunId, run)
      stagesByRun.set(workflowRunId, stages)
      startedAt.set(workflowRunId, startedAtMs)
      forcedCompleted.delete(workflowRunId)
      activeWorkflowRunId = workflowRunId
      return run
    },
    skipActive() {
      if (activeWorkflowRunId) forcedCompleted.add(activeWorkflowRunId)
    },
    progress(workflowRunId: string) {
      const run = runs.get(workflowRunId)
      const startedAtMs = startedAt.get(workflowRunId)
      if (!run || startedAtMs == null) throw new Error('WORKFLOW_NOT_FOUND')
      const stages = stagesByRun.get(workflowRunId)
      if (!stages) throw new Error('WORKFLOW_STAGES_NOT_FOUND')
      const durationMs = stages.reduce((sum, stage) => sum + stage.duration_ms, 0)
      const elapsedMs = forcedCompleted.has(workflowRunId)
        ? durationMs
        : (Date.now() - startedAtMs) / timeScale
      const progress = buildSimulationWorkflowProgress(run, elapsedMs, stages)
      if (progress.poll_after_ms == null && activeWorkflowRunId === workflowRunId) activeWorkflowRunId = null
      return progress.poll_after_ms == null
        ? progress
        : { ...progress, poll_after_ms: Math.max(1, Math.round(progress.poll_after_ms * timeScale)) }
    },
  }
}

function isoAt(workflow: WorkflowRun, elapsedMs: number) {
  const start = Date.parse(workflow.created_at)
  return new Date((Number.isFinite(start) ? start : Date.now()) + elapsedMs).toISOString()
}

export function buildSimulationWorkflowProgress(
  workflow: WorkflowRun,
  elapsedMs: number,
  stageDefinitions: readonly SimulationWorkflowStage[] = simulationWorkflowStages,
): WorkflowProgress & { stages: WorkflowStageProgress[] } {
  const boundedElapsed = Math.max(0, elapsedMs)
  let cursor = 0
  let completed = 0
  let activeStageCode: string | null = null

  const durationMs = stageDefinitions.reduce((sum, stage) => sum + stage.duration_ms, 0)
  const stages: WorkflowStageProgress[] = stageDefinitions.map((stage) => {
    const start = cursor
    const end = cursor + stage.duration_ms
    cursor = end
    const stageRunId = `${workflow.workflow_run_id}:${stage.stage_code.toLocaleLowerCase()}`

    if (boundedElapsed >= end) {
      completed += 1
      return {
        stage_run_id: stageRunId,
        stage_code: stage.stage_code,
        status: 'SUCCEEDED',
        attempt: 1,
        reason_codes: [],
        failure_code: null,
        updated_at: isoAt(workflow, end),
        completed_at: isoAt(workflow, end),
      }
    }

    if (boundedElapsed >= start && activeStageCode === null) {
      activeStageCode = stage.stage_code
      return {
        stage_run_id: stageRunId,
        stage_code: stage.stage_code,
        status: 'RUNNING',
        attempt: 1,
        reason_codes: [],
        failure_code: null,
        updated_at: isoAt(workflow, boundedElapsed),
        completed_at: null,
      }
    }

    return {
      stage_run_id: stageRunId,
      stage_code: stage.stage_code,
      status: 'PENDING',
      attempt: 0,
      reason_codes: [],
      failure_code: null,
      updated_at: workflow.created_at,
      completed_at: null,
    }
  })

  const terminal = boundedElapsed >= durationMs
  return {
    ...workflow,
    status: terminal ? 'SUCCEEDED' : 'RUNNING',
    updated_at: isoAt(workflow, Math.min(boundedElapsed, durationMs)),
    stages,
    completed_stage_count: terminal ? stageDefinitions.length : completed,
    total_stage_count: stageDefinitions.length,
    current_stage_codes: terminal || activeStageCode === null ? [] : [activeStageCode],
    terminal_reason_codes: [],
    human_review_requests: [],
    poll_after_ms: terminal ? null : 1_250,
  }
}
