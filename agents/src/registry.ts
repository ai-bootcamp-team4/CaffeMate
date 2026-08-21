import type { AgentName, TaskType } from './types'

export const AGENT_MODEL = Object.freeze({
  id: 'gemini-3.7-flash',
  region: 'asia-northeast3',
  networkEnabled: false,
  allowGlobalFallback: false,
  thinkingLevel: 'medium',
} as const)

export interface TaskRegistration {
  agentName: AgentName
  promptVersion: string
  inputSchemaId: string
  outputSchemaId: string
  deadlineSeconds: number
  maxOutputTokens: number
}

export const TASK_REGISTRY: Readonly<Record<TaskType, TaskRegistration>> = Object.freeze({
  INTENT_DELTA: {
    agentName: 'INTENT_INTERPRETER',
    promptVersion: 'intent-interpreter.v1',
    inputSchemaId: 'caffemate.agent.intent-input.v1',
    outputSchemaId: 'caffemate.agent.intent-result.v1',
    deadlineSeconds: 15,
    maxOutputTokens: 2048,
  },
  EVIDENCE_PLAN: {
    agentName: 'EVIDENCE_RESEARCHER',
    promptVersion: 'evidence-researcher.v1',
    inputSchemaId: 'caffemate.agent.evidence-plan-input.v1',
    outputSchemaId: 'caffemate.agent.evidence-plan-result.v1',
    deadlineSeconds: 20,
    maxOutputTokens: 4096,
  },
  EVIDENCE_ASSESS: {
    agentName: 'EVIDENCE_RESEARCHER',
    promptVersion: 'evidence-researcher.v1',
    inputSchemaId: 'caffemate.agent.evidence-assess-input.v1',
    outputSchemaId: 'caffemate.agent.evidence-assess-result.v1',
    deadlineSeconds: 30,
    maxOutputTokens: 8192,
  },
  PROPOSE_INDEPENDENT: {
    agentName: 'PROPOSAL_AGENT',
    promptVersion: 'proposal-agent.v1',
    inputSchemaId: 'caffemate.agent.independent-proposal-input.v1',
    outputSchemaId: 'caffemate.agent.independent-proposal-result.v1',
    deadlineSeconds: 30,
    maxOutputTokens: 8192,
  },
  PROPOSE_FRANCHISE: {
    agentName: 'PROPOSAL_AGENT',
    promptVersion: 'proposal-agent.v1',
    inputSchemaId: 'caffemate.agent.franchise-proposal-input.v1',
    outputSchemaId: 'caffemate.agent.franchise-proposal-result.v1',
    deadlineSeconds: 30,
    maxOutputTokens: 8192,
  },
  DOCUMENT_EXTRACT: {
    agentName: 'DOCUMENT_ANALYST',
    promptVersion: 'document-analyst.v1',
    inputSchemaId: 'caffemate.agent.document-extract-input.v1',
    outputSchemaId: 'caffemate.agent.document-extract-result.v1',
    deadlineSeconds: 60,
    maxOutputTokens: 8192,
  },
  CANDIDATE_AUDIT: {
    agentName: 'TYPED_CANDIDATE_AUDITOR',
    promptVersion: 'typed-candidate-auditor.v1',
    inputSchemaId: 'caffemate.agent.candidate-audit-input.v1',
    outputSchemaId: 'caffemate.agent.candidate-audit-result.v1',
    deadlineSeconds: 20,
    maxOutputTokens: 6144,
  },
})
