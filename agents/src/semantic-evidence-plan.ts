import type { AgentTask, AgentTaskResult } from './types'

export interface EvidencePlanSemanticIssue {
  code: string
  path: string
  message: string
}

type JsonObject = Record<string, unknown>

function object(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {}
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function strings(value: unknown): string[] {
  return array(value).filter((item): item is string => typeof item === 'string')
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value as JsonObject)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, canonical(item)]),
  )
}

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right))
}

function add(issues: EvidencePlanSemanticIssue[], code: string, path: string, message: string): void {
  issues.push({ code, path, message })
}

export function validateEvidencePlanSemantics(
  task: AgentTask,
  result: AgentTaskResult,
): EvidencePlanSemanticIssue[] {
  if (task.task_type !== 'EVIDENCE_PLAN' || !result.payload || typeof result.payload !== 'object' || Array.isArray(result.payload)) {
    return []
  }
  const taskPayload = object(task.payload)
  const resultPayload = object(result.payload)
  const claims = new Map<string, JsonObject>()
  for (const rawClaim of array(taskPayload.claims)) {
    const claim = object(rawClaim)
    if (typeof claim.claim_id === 'string') claims.set(claim.claim_id, claim)
  }
  const plans = array(resultPayload.claim_plans).map(object)
  const issues: EvidencePlanSemanticIssue[] = []

  if (result.status === 'COMPLETE') {
    const producedClaimIds = plans
      .map((plan) => plan.claim_id)
      .filter((value): value is string => typeof value === 'string')
    if (producedClaimIds.length !== new Set(producedClaimIds).size
      || producedClaimIds.length !== claims.size
      || producedClaimIds.some((claimId) => !claims.has(claimId))) {
      add(
        issues,
        'EVIDENCE_PLAN_CLAIM_COVERAGE_INVALID',
        '/payload/claim_plans',
        'complete Evidence Plan must cover every input claim exactly once',
      )
    }
  }

  const constraints = object(taskPayload.planning_constraints)
  const allowedTools = new Set(strings(constraints.allowed_tools))
  const actionPool = new Set(strings(taskPayload.action_id_pool))
  const catalog = new Map<string, string>()
  for (const rawTool of array(task.available_tool_catalog)) {
    const tool = object(rawTool)
    if (typeof tool.tool_name === 'string' && typeof tool.tool_version === 'string') {
      catalog.set(tool.tool_name, tool.tool_version)
    }
  }
  const maxPerClaim = typeof constraints.max_actions_per_claim === 'number' ? constraints.max_actions_per_claim : 0
  const maxTotal = typeof constraints.max_total_actions === 'number' ? constraints.max_total_actions : 0
  const actionIds: string[] = []
  let totalActions = 0

  for (const [planIndex, plan] of plans.entries()) {
    const planClaimId = plan.claim_id
    const claim = typeof planClaimId === 'string' ? claims.get(planClaimId) : undefined
    const supportActions = array(plan.support_actions)
    const counterActions = array(plan.counter_actions)
    if (plan.route !== 'SQL' && supportActions.length === 0) {
      add(issues, 'SUPPORT_ACTION_REQUIRED', `/payload/claim_plans/${planIndex}/support_actions`, 'non-SQL material claims require an explicit support action')
    }
    if (plan.route !== 'SQL' && counterActions.length === 0) {
      add(issues, 'COUNTEREVIDENCE_ACTION_REQUIRED', `/payload/claim_plans/${planIndex}/counter_actions`, 'non-SQL material claims require an explicit counterevidence action')
    }

    const groupedActions = [
      ['support_actions', 'SUPPORT', supportActions],
      ['counter_actions', 'COUNTER', counterActions],
    ] as const
    const planActionCount = supportActions.length + counterActions.length
    totalActions += planActionCount
    if (maxPerClaim > 0 && planActionCount > maxPerClaim) {
      add(issues, 'EVIDENCE_PLAN_ACTION_LIMIT_EXCEEDED', `/payload/claim_plans/${planIndex}`, 'claim action count exceeds planning constraints')
    }

    for (const [collection, expectedPolarity, actions] of groupedActions) {
      for (const [actionIndex, rawAction] of actions.entries()) {
        const action = object(rawAction)
        const path = `/payload/claim_plans/${planIndex}/${collection}/${actionIndex}`
        if (typeof action.action_id !== 'string' || !actionPool.has(action.action_id)) {
          add(issues, 'OUTPUT_ID_NOT_IN_POOL', `${path}/action_id`, `output id ${String(action.action_id)} was not supplied by the controller`)
        } else {
          actionIds.push(action.action_id)
        }
        const toolName = action.tool_name
        if (typeof toolName !== 'string'
          || !allowedTools.has(toolName)
          || catalog.get(toolName) !== action.tool_version) {
          add(issues, 'EVIDENCE_PLAN_TOOL_NOT_ALLOWED', path, 'planned tool name/version is outside the pinned allowed catalog')
        }
        if (action.claim_id !== planClaimId
          || action.polarity !== expectedPolarity
          || !claim
          || !sameJson(action.scope_constraints, claim.geographic_scope)) {
          add(issues, 'EVIDENCE_PLAN_ACTION_CONTEXT_MISMATCH', path, 'action claim, polarity, or geographic scope does not match its claim plan')
        }
      }
    }
  }

  if (actionIds.length !== new Set(actionIds).size) {
    add(issues, 'EVIDENCE_PLAN_ACTION_DUPLICATED', '/payload/claim_plans', 'planned action ids must be globally unique')
  }
  if (maxTotal > 0 && totalActions > maxTotal) {
    add(issues, 'EVIDENCE_PLAN_ACTION_LIMIT_EXCEEDED', '/payload/claim_plans', 'total action count exceeds planning constraints')
  }
  return issues
}
