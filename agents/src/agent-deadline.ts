import type { AgentTask } from './types'

export const MIN_REPAIR_REMAINING_MS = 2_000

export class AgentDeadlineError extends Error {
  readonly code = 'RUNTIME_TIMED_OUT'

  constructor(message = 'logical AgentTask deadline budget is exhausted') {
    super(`RUNTIME_TIMED_OUT: ${message}`)
    this.name = 'AgentDeadlineError'
  }
}

export function remainingAgentTaskMilliseconds(task: AgentTask, nowMs = Date.now()): number {
  const deadlineMs = Date.parse(task.deadline_at)
  if (!Number.isFinite(deadlineMs)) throw new AgentDeadlineError('logical AgentTask deadline is invalid')
  return deadlineMs - nowMs
}

export function ensureAgentTaskDeadline(
  task: AgentTask,
  minimumRemainingMs = 1,
  nowMs = Date.now(),
): void {
  if (remainingAgentTaskMilliseconds(task, nowMs) < minimumRemainingMs) {
    throw new AgentDeadlineError()
  }
}

export function agentTaskDeadlineSignal(task: AgentTask): AbortSignal {
  const remainingMs = Math.floor(remainingAgentTaskMilliseconds(task))
  if (remainingMs <= 0) throw new AgentDeadlineError()
  return AbortSignal.timeout(remainingMs)
}
