import type { AgentName, TaskType } from './types'

export const GCP_LOCATIONS = Object.freeze({
  runtime: 'asia-northeast3',
  generation: 'global',
  rag: 'asia-northeast3',
  embedding: 'asia-northeast3',
} as const)

export const AGENT_MODEL = Object.freeze({
  id: 'gemini-3.7-flash',
  approvalStatus: 'APPROVED',
  region: GCP_LOCATIONS.generation,
  networkEnabled: true,
  allowGlobalFallback: false,
  thinkingLevel: 'high',
} as const)

export type AgentThinkingLevel = 'low' | 'medium' | 'high'

export interface TaskRegistration {
  agentName: AgentName
  promptVersion: string
  inputSchemaId: string
  outputSchemaId: string
  deadlineSeconds: number
  thinkingLevel: AgentThinkingLevel
  maxOutputTokens: number
}

export const TASK_REGISTRY: Readonly<Record<TaskType, TaskRegistration>> = Object.freeze({
  INTENT_DELTA: {
    agentName: 'INTENT_INTERPRETER',
    promptVersion: 'intent-interpreter.v2',
    inputSchemaId: 'caffemate.agent.intent-input.v1',
    outputSchemaId: 'caffemate.agent.intent-result.v1',
    deadlineSeconds: 30,
    thinkingLevel: 'low',
    maxOutputTokens: 4096,
  },
  EVIDENCE_PLAN: {
    agentName: 'EVIDENCE_RESEARCHER',
    promptVersion: 'evidence-researcher.v1',
    inputSchemaId: 'caffemate.agent.evidence-plan-input.v1',
    outputSchemaId: 'caffemate.agent.evidence-plan-result.v1',
    deadlineSeconds: 60,
    thinkingLevel: 'low',
    maxOutputTokens: 8192,
  },
  EVIDENCE_ASSESS: {
    agentName: 'EVIDENCE_RESEARCHER',
    promptVersion: 'evidence-assessor.v2',
    inputSchemaId: 'caffemate.agent.evidence-assess-input.v1',
    outputSchemaId: 'caffemate.agent.evidence-assess-result.v1',
    deadlineSeconds: 60,
    thinkingLevel: 'low',
    maxOutputTokens: 16384,
  },
  PROPOSE_INDEPENDENT: {
    agentName: 'PROPOSAL_AGENT',
    promptVersion: 'proposal-agent.v2',
    inputSchemaId: 'caffemate.agent.independent-proposal-input.v1',
    outputSchemaId: 'caffemate.agent.independent-proposal-result.v1',
    deadlineSeconds: 60,
    thinkingLevel: 'low',
    maxOutputTokens: 4096,
  },
  PROPOSE_FRANCHISE: {
    agentName: 'PROPOSAL_AGENT',
    promptVersion: 'proposal-agent.v2',
    inputSchemaId: 'caffemate.agent.franchise-proposal-input.v1',
    outputSchemaId: 'caffemate.agent.franchise-proposal-result.v1',
    deadlineSeconds: 60,
    thinkingLevel: 'low',
    maxOutputTokens: 4096,
  },
  DOCUMENT_EXTRACT: {
    agentName: 'DOCUMENT_ANALYST',
    promptVersion: 'document-analyst.v1',
    inputSchemaId: 'caffemate.agent.document-extract-input.v1',
    outputSchemaId: 'caffemate.agent.document-extract-result.v1',
    deadlineSeconds: 60,
    thinkingLevel: 'medium',
    maxOutputTokens: 8192,
  },
  CANDIDATE_AUDIT: {
    agentName: 'TYPED_CANDIDATE_AUDITOR',
    promptVersion: 'typed-candidate-auditor.v1',
    inputSchemaId: 'caffemate.agent.candidate-audit-input.v1',
    outputSchemaId: 'caffemate.agent.candidate-audit-result.v1',
    deadlineSeconds: 60,
    thinkingLevel: 'low',
    maxOutputTokens: 4096,
  },
})
