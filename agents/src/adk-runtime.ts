import { BaseAgent, createEvent, type Event, type InvocationContext } from '@google/adk'
import { dispatchAgentTask, validateAgentTaskForDispatch } from './dispatcher'
import { canonicalizeJson } from './input-digest'
import {
  AgentModelError,
  createModelExecutors,
  type AgentModelClient,
  type ApprovedAgentModelConfig,
} from './model-executor'
import { TASK_REGISTRY } from './registry'
import type { AgentName, AgentTask } from './types'

export class AdkRuntimeError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'AdkRuntimeError'
  }
}

export type ApprovedModelProvider = () => ApprovedAgentModelConfig | undefined

export interface CaffeMateAdkDependencies {
  modelClient: AgentModelClient
  approvedModel: ApprovedModelProvider
}

interface RoleAgentConfig {
  name: AgentName
  description: string
  modelClient: AgentModelClient
  approvedModel: ApprovedModelProvider
}

function parseTask(context: InvocationContext): AgentTask {
  const content = context.userContent
  if (content?.role !== 'user' || content.parts?.length !== 1) {
    throw new AdkRuntimeError('RUNTIME_REQUEST_INVALID', 'request must contain exactly one user text part')
  }
  const part = content.parts[0]
  if (!part || typeof part.text !== 'string' || !part.text.trim()) {
    throw new AdkRuntimeError('RUNTIME_REQUEST_INVALID', 'request must contain exactly one non-empty user text part')
  }
  const populatedPartKeys = Object.entries(part)
    .filter(([, value]) => value !== undefined)
    .map(([key]) => key)
  if (populatedPartKeys.some((key) => key !== 'text')) {
    throw new AdkRuntimeError('RUNTIME_REQUEST_INVALID', 'request text part cannot include function, binary, or metadata payloads')
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(part.text)
  } catch {
    throw new AdkRuntimeError('RUNTIME_REQUEST_INVALID', 'request text must be one JSON AgentTask object')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new AdkRuntimeError('RUNTIME_REQUEST_INVALID', 'request text must be one JSON AgentTask object')
  }
  return parsed as AgentTask
}

class CaffeMateRoleAgent extends BaseAgent<RoleAgentConfig> {
  private readonly modelClient: AgentModelClient
  private readonly approvedModel: ApprovedModelProvider

  constructor(config: RoleAgentConfig) {
    super(config)
    this.modelClient = config.modelClient
    this.approvedModel = config.approvedModel
  }

  protected async *runAsyncImpl(context: InvocationContext): AsyncGenerator<Event, void, void> {
    const task = parseTask(context)
    if (task.agent_name !== this.name) {
      throw new AdkRuntimeError('RUNTIME_AGENT_MISMATCH', `child ${this.name} cannot execute task for ${task.agent_name}`)
    }
    const approvedModel = this.approvedModel()
    if (!approvedModel) {
      throw new AgentModelError('MODEL_NOT_APPROVED', 'an approved model id is required after the regional GCP preflight')
    }

    const result = await dispatchAgentTask(task, createModelExecutors(this.modelClient, approvedModel))
    yield createEvent({
      invocationId: context.invocationId,
      author: this.name,
      branch: context.branch,
      content: {
        role: 'model',
        parts: [{ text: canonicalizeJson(result) }],
      },
    })
  }

  // ADK requires an async-generator override even though CaffeMate rejects live mode before producing an event.
  // eslint-disable-next-line require-yield
  protected async *runLiveImpl(): AsyncGenerator<Event, void, void> {
    throw new AdkRuntimeError('RUNTIME_LIVE_UNSUPPORTED', 'CaffeMate Agent Runtime accepts text AgentTask requests only')
  }
}

class CaffeMateTaskDispatcher extends BaseAgent {
  protected async *runAsyncImpl(context: InvocationContext): AsyncGenerator<Event, void, void> {
    const task = parseTask(context)
    validateAgentTaskForDispatch(task)
    const registration = TASK_REGISTRY[task.task_type]
    const child = this.subAgents.find((candidate) => candidate.name === registration.agentName)
    if (!child) {
      throw new AdkRuntimeError('RUNTIME_AGENT_NOT_REGISTERED', `missing ADK child ${registration.agentName}`)
    }
    yield* child.runAsync(context)
  }

  // ADK requires an async-generator override even though CaffeMate rejects live mode before producing an event.
  // eslint-disable-next-line require-yield
  protected async *runLiveImpl(): AsyncGenerator<Event, void, void> {
    throw new AdkRuntimeError('RUNTIME_LIVE_UNSUPPORTED', 'CaffeMate Agent Runtime accepts text AgentTask requests only')
  }
}

const ROLE_DESCRIPTIONS: Readonly<Record<AgentName, string>> = Object.freeze({
  INTENT_INTERPRETER: 'Produces typed intent deltas from a pinned AgentTask.',
  EVIDENCE_RESEARCHER: 'Produces typed evidence plans and assessments without executing tools.',
  PROPOSAL_AGENT: 'Produces typed independent or franchise candidate proposals.',
  DOCUMENT_ANALYST: 'Produces typed document extraction proposals.',
  TYPED_CANDIDATE_AUDITOR: 'Audits typed candidate payloads without authoritative writes.',
})

export function createCaffeMateAdkRoot(dependencies: CaffeMateAdkDependencies): BaseAgent {
  const subAgents = (Object.keys(ROLE_DESCRIPTIONS) as AgentName[]).map((name) => new CaffeMateRoleAgent({
    name,
    description: ROLE_DESCRIPTIONS[name],
    modelClient: dependencies.modelClient,
    approvedModel: dependencies.approvedModel,
  }))
  return new CaffeMateTaskDispatcher({
    name: 'CAFFEMATE_TASK_DISPATCHER',
    description: 'Deterministically dispatches validated CaffeMate AgentTask objects to one typed role child.',
    subAgents,
  })
}