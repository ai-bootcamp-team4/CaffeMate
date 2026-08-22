import { GCP_LOCATIONS } from '../../agents/src/registry'

export const RAG_RANKER = Object.freeze({
  id: 'semantic-ranker-default-004',
  approvalStatus: 'APPROVED',
  region: GCP_LOCATIONS.rag,
  allowGlobalFallback: false,
} as const)