export const RAG_REGION = 'asia-northeast3' as const

export const RAG_RANKER = Object.freeze({
  id: 'semantic-ranker-default-004',
  approvalStatus: 'APPROVED',
  region: RAG_REGION,
  allowGlobalFallback: false,
} as const)
