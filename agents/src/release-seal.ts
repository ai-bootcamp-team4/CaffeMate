import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import agentRolePayloadsSchema from '../../docs/contracts/agent-role-payloads.schema.json'
import agentTaskResultSchema from '../../docs/contracts/agent-task-result.schema.json'
import agentTaskSchema from '../../docs/contracts/agent-task.schema.json'
import candidateResultSchema from '../../docs/contracts/candidate-result.schema.json'
import commonTypesSchema from '../../docs/contracts/common-types.schema.json'
import evidenceRecordSchema from '../../docs/contracts/evidence-record.schema.json'
import mcpToolContractsSchema from '../../docs/contracts/mcp-tool-contracts.schema.json'
import { canonicalizeJson } from './input-digest'
import { PROMPTS } from './prompts'
import { AGENT_MODEL, GCP_LOCATIONS, TASK_REGISTRY } from './registry'

export interface ReleaseTaskPin {
  agent_name: string
  prompt_version: string
  input_schema_id: string
  output_schema_id: string
  deadline_seconds: number
}

export interface ReleaseSourceRevisionPin {
  document_revision_id: string
  rag_file_resource_name: string
  content_digest: string
}

export interface ReleaseIndexGenerationPin {
  generation_id: string
  status: 'BUILDING' | 'EVALUATING' | 'SHADOW' | 'ACTIVE' | 'FAILED'
  corpus_resource_name: string
  parser_revision: string
  schema_version: string
  embedding_model_id: string
  reranker_id: string
  source_revisions: ReleaseSourceRevisionPin[]
  sealed_evaluation_digest: string | null
}

export interface AgentReleaseManifest {
  schema_version: string
  runtime_region: string
  runtime: {
    resource_name: string
    image_uri: string
  }
  model: {
    id: string
    approval_status: string
    thinking_level: string
    region: string
  }
  allow_global_fallback: boolean
  network_mode: string
  prompt_bundle_digest: string
  agent_contract_bundle_digest: string
  mcp_manifest_digest: string
  index_generation: ReleaseIndexGenerationPin
  tasks: Record<string, ReleaseTaskPin>
}

export interface ReleaseSealIssue {
  code: string
  detail: string
}

export interface ReleaseSourceSealResult {
  ok: boolean
  issues: ReleaseSealIssue[]
}

export interface RuntimeReleaseIdentity {
  schema_version: '1.0.0'
  prompt_bundle_digest: string
  agent_contract_bundle_digest: string
}

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/
const CORPUS_RESOURCE = /^projects\/[^/]+\/locations\/[^/]+\/ragCorpora\/[^/]+$/

const AGENT_CONTRACT_BUNDLE = Object.freeze({
  commonTypesSchema,
  evidenceRecordSchema,
  candidateResultSchema,
  mcpToolContractsSchema,
  agentRolePayloadsSchema,
  agentTaskSchema,
  agentTaskResultSchema,
})

function digest(value: unknown): string {
  return `sha256:${createHash('sha256').update(canonicalizeJson(value), 'utf8').digest('hex')}`
}

function checkedInMcpManifestDigest(): string {
  const [hex] = readFileSync('docs/contracts/mcp-tool-manifest.sha256', 'utf8').trim().split(/\s+/)
  if (!hex || !/^[0-9a-f]{64}$/.test(hex)) {
    throw new Error('RELEASE_MCP_MANIFEST_DIGEST_SOURCE_INVALID')
  }
  return `sha256:${hex}`
}

export function computePromptBundleDigest(): string {
  return digest({ bundle: 'caffemate.agent-prompts.v1', prompts: PROMPTS })
}

export function computeAgentContractBundleDigest(): string {
  return digest({ bundle: 'caffemate.agent-contracts.v1', schemas: AGENT_CONTRACT_BUNDLE })
}

export function runtimeReleaseIdentity(): RuntimeReleaseIdentity {
  return {
    schema_version: '1.0.0',
    prompt_bundle_digest: computePromptBundleDigest(),
    agent_contract_bundle_digest: computeAgentContractBundleDigest(),
  }
}

function issue(issues: ReleaseSealIssue[], code: string, detail: string): void {
  issues.push({ code, detail })
}

function verifyTaskPins(manifest: AgentReleaseManifest, issues: ReleaseSealIssue[]): void {
  const expectedTaskTypes = Object.keys(TASK_REGISTRY).sort()
  const actualTaskTypes = Object.keys(manifest.tasks).sort()
  if (canonicalizeJson(actualTaskTypes) !== canonicalizeJson(expectedTaskTypes)) {
    issue(issues, 'RELEASE_TASK_SET_MISMATCH', `expected ${expectedTaskTypes.join(',')} but found ${actualTaskTypes.join(',')}`)
    return
  }

  for (const [taskType, registration] of Object.entries(TASK_REGISTRY)) {
    const pin = manifest.tasks[taskType]
    const expected = {
      agent_name: registration.agentName,
      prompt_version: registration.promptVersion,
      input_schema_id: registration.inputSchemaId,
      output_schema_id: registration.outputSchemaId,
      deadline_seconds: registration.deadlineSeconds,
    }
    if (canonicalizeJson(pin) !== canonicalizeJson(expected)) {
      issue(issues, 'RELEASE_TASK_PIN_MISMATCH', `${taskType} does not match the runtime registry`)
    }
  }
}

function verifyIndexGenerationPin(pin: ReleaseIndexGenerationPin, issues: ReleaseSealIssue[]): void {
  if (pin.status !== 'ACTIVE') {
    issue(issues, 'RELEASE_INDEX_GENERATION_NOT_ACTIVE', `generation ${pin.generation_id} is ${pin.status}`)
  }
  if (!CORPUS_RESOURCE.test(pin.corpus_resource_name)) {
    issue(issues, 'RELEASE_INDEX_CORPUS_INVALID', pin.corpus_resource_name)
  }
  if (!pin.parser_revision || !pin.schema_version || !pin.embedding_model_id || !pin.reranker_id) {
    issue(issues, 'RELEASE_INDEX_CONFIGURATION_INCOMPLETE', `generation ${pin.generation_id} is missing immutable configuration`)
  }
  if (!pin.sealed_evaluation_digest || !SHA256_DIGEST.test(pin.sealed_evaluation_digest)) {
    issue(issues, 'RELEASE_INDEX_EVALUATION_UNSEALED', `generation ${pin.generation_id} has no valid sealed evaluation digest`)
  }
  if (pin.source_revisions.length === 0) {
    issue(issues, 'RELEASE_INDEX_SOURCE_SET_EMPTY', `generation ${pin.generation_id} has no source revisions`)
    return
  }

  const revisionIds = new Set<string>()
  const ragFiles = new Set<string>()
  for (const source of pin.source_revisions) {
    if (!source.document_revision_id || !SHA256_DIGEST.test(source.content_digest)) {
      issue(issues, 'RELEASE_INDEX_SOURCE_INVALID', source.document_revision_id || 'missing document revision id')
    }
    if (!source.rag_file_resource_name.startsWith(`${pin.corpus_resource_name}/ragFiles/`)) {
      issue(issues, 'RELEASE_INDEX_RAG_FILE_MISMATCH', source.rag_file_resource_name)
    }
    if (revisionIds.has(source.document_revision_id) || ragFiles.has(source.rag_file_resource_name)) {
      issue(issues, 'RELEASE_INDEX_SOURCE_DUPLICATE', source.document_revision_id)
    }
    revisionIds.add(source.document_revision_id)
    ragFiles.add(source.rag_file_resource_name)
  }
}

export function verifyReleaseSourceSeal(manifest: AgentReleaseManifest): ReleaseSourceSealResult {
  const issues: ReleaseSealIssue[] = []

  if (manifest.prompt_bundle_digest !== computePromptBundleDigest()) {
    issue(issues, 'RELEASE_PROMPT_BUNDLE_MISMATCH', 'prompt contents differ from the release pin')
  }
  if (manifest.agent_contract_bundle_digest !== computeAgentContractBundleDigest()) {
    issue(issues, 'RELEASE_AGENT_CONTRACT_BUNDLE_MISMATCH', 'Agent contract contents differ from the release pin')
  }
  if (manifest.model.id !== AGENT_MODEL.id
    || manifest.model.approval_status !== AGENT_MODEL.approvalStatus
    || manifest.model.region !== AGENT_MODEL.region
    || manifest.model.thinking_level !== AGENT_MODEL.thinkingLevel) {
    issue(issues, 'RELEASE_MODEL_MISMATCH', 'model release pin differs from the approved runtime registry')
  }
  if (manifest.runtime_region !== GCP_LOCATIONS.runtime) {
    issue(issues, 'RELEASE_RUNTIME_REGION_MISMATCH', manifest.runtime_region)
  }
  if (manifest.allow_global_fallback !== false) {
    issue(issues, 'RELEASE_FALLBACK_POLICY_MISMATCH', 'global fallback must remain disabled')
  }
  if (!SHA256_DIGEST.test(manifest.mcp_manifest_digest)) {
    issue(issues, 'RELEASE_MCP_MANIFEST_DIGEST_INVALID', manifest.mcp_manifest_digest)
  } else if (manifest.mcp_manifest_digest !== checkedInMcpManifestDigest()) {
    issue(issues, 'RELEASE_MCP_MANIFEST_MISMATCH', 'MCP tool manifest contents differ from the release pin')
  }

  verifyTaskPins(manifest, issues)
  verifyIndexGenerationPin(manifest.index_generation, issues)
  return { ok: issues.length === 0, issues }
}