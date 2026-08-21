import fixtureMatrix from '../fixtures/task-matrix.json'
import { dispatchAgentTask } from './dispatcher'
import { AGENT_MODEL, TASK_REGISTRY } from './registry'
import { validateAgentTask, validateAgentTaskResult } from './schema-validator'
import { validateAgentSemantics } from './semantic-validator'
import type { AgentExecutorMap, AgentTask, AgentTaskResult } from './types'

export interface AgentControlOutput {
  ok: boolean
  code?: string
  message?: string
  data?: unknown
}

type FixtureCase = { id: string; task: AgentTask; result: AgentTaskResult }

const fixtures = fixtureMatrix.cases as unknown as FixtureCase[]

function invalid(code: string, message: string): AgentControlOutput {
  return { ok: false, code, message }
}

function fixtureValidation(fixture: FixtureCase) {
  const task = validateAgentTask(fixture.task)
  const result = validateAgentTaskResult(fixture.result)
  const semantics = task.ok && result.ok ? validateAgentSemantics(fixture.task, fixture.result) : { ok: false as const, issues: [] }
  return { id: fixture.id, task, result, semantics, ok: task.ok && result.ok && semantics.ok }
}

export async function runAgentControl(args: string[]): Promise<AgentControlOutput> {
  const [command, target] = args
  switch (command) {
    case 'registry':
      return { ok: true, data: { model: AGENT_MODEL, tasks: TASK_REGISTRY } }
    case 'validate-fixtures': {
      const validations = fixtures.map(fixtureValidation)
      const invalidCases = validations.filter((item) => !item.ok)
      return {
        ok: invalidCases.length === 0,
        code: invalidCases.length ? 'FIXTURE_VALIDATION_FAILED' : undefined,
        data: { total: validations.length, invalid: invalidCases.length, cases: invalidCases },
      }
    }
    case 'dispatch-fixture': {
      if (!target) return invalid('FIXTURE_ID_REQUIRED', 'dispatch-fixture requires a fixture id')
      const fixture = fixtures.find((item) => item.id === target)
      if (!fixture) return invalid('FIXTURE_NOT_FOUND', `fixture ${target} does not exist`)
      const executors = {
        [fixture.task.agent_name]: async () => fixture.result,
      } as AgentExecutorMap
      try {
        const result = await dispatchAgentTask(fixture.task, executors)
        return { ok: true, data: result }
      } catch (error) {
        return invalid('FIXTURE_DISPATCH_FAILED', error instanceof Error ? error.message : String(error))
      }
    }
    default:
      return invalid('COMMAND_NOT_SUPPORTED', `supported commands: registry, validate-fixtures, dispatch-fixture <id>`)
  }
}
