import { createCaffeMateAdkRoot } from '../src/adk-runtime'
import type { ApprovedAgentModelConfig } from '../src/model-executor'
import { AGENT_MODEL } from '../src/registry'
import { createApplicationDefaultVertexAgentModelClient } from '../src/vertex-model-client'

function approvedModel(): ApprovedAgentModelConfig | undefined {
  const modelId: string | null = AGENT_MODEL.id
  const approvalStatus: string = AGENT_MODEL.approvalStatus
  if (!modelId || approvalStatus !== 'APPROVED') return undefined
  return {
    id: modelId,
    region: AGENT_MODEL.region,
    thinkingLevel: AGENT_MODEL.thinkingLevel,
  }
}

export const rootAgent = createCaffeMateAdkRoot({
  modelClient: createApplicationDefaultVertexAgentModelClient(),
  approvedModel,
})
