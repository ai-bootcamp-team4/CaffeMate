import fixtureMatrix from '../fixtures/task-matrix.json'
import releaseManifest from '../release-manifest.json'
import { dispatchAgentTask } from './dispatcher'
import { createApplicationDefaultGoogleCloudContext, type GoogleCloudContext } from './gcp-auth'
import { runGcpPreflight, type GcpPreflightResult } from './gcp-preflight'
import { verifyReleaseSourceSeal, type AgentReleaseManifest } from './release-seal'
import { AGENT_MODEL, GCP_LOCATIONS, TASK_REGISTRY } from './registry'
import { AGENT_RUNTIME_CLASS_METHODS, CAFFEMATE_AGENT_APP_NAME } from './runtime-contract'
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

export interface AgentControlDependencies {
  gcpPreflight?: (modelId?: string) => Promise<GcpPreflightResult>
}

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

export async function runDefaultGcpPreflight(
  modelId?: string,
  cloud: GoogleCloudContext = createApplicationDefaultGoogleCloudContext(),
): Promise<GcpPreflightResult> {
  const sourceSeal = verifyReleaseSourceSeal(releaseManifest as AgentReleaseManifest)
  if (!sourceSeal.ok) {
    throw new Error(`RELEASE_SOURCE_SEAL_INVALID: ${sourceSeal.issues.map((issue) => issue.code).join(',')}`)
  }

  const projectId = await cloud.projectId()
  return runGcpPreflight({
    projectId,
    runtimeRegion: GCP_LOCATIONS.runtime,
    generationRegion: GCP_LOCATIONS.generation,
    ragRegion: GCP_LOCATIONS.rag,
    embeddingRegion: GCP_LOCATIONS.embedding,
    approvedModelId: modelId ?? AGENT_MODEL.id,
    runtimePin: {
      resourceName: releaseManifest.runtime.resource_name,
      imageUri: releaseManifest.runtime.image_uri,
      promptBundleDigest: releaseManifest.prompt_bundle_digest,
      agentContractBundleDigest: releaseManifest.agent_contract_bundle_digest,
    },
    mcpPin: {
      serviceName: releaseManifest.mcp.runtime.service_name,
      region: releaseManifest.mcp.runtime.region,
      sourceRevision: releaseManifest.mcp.runtime.source_revision,
      imageUri: releaseManifest.mcp.runtime.image_uri,
    },
    ragPin: {
      corpusResourceName: releaseManifest.index_generation.corpus_resource_name,
      ragFileResourceNames: releaseManifest.index_generation.source_revisions.map((source) => source.rag_file_resource_name),
      embeddingModelId: releaseManifest.index_generation.embedding_model_id,
      rerankerId: releaseManifest.index_generation.reranker_id,
      sourceRevisions: releaseManifest.index_generation.source_revisions.map((source) => ({
        sourceFamily: source.source_family,
        sourceDate: source.source_date,
        sourceUri: source.source_uri,
        gcsObjectGeneration: source.gcs_object_generation,
        ragFileResourceName: source.rag_file_resource_name,
      })),
    },
    accessToken: cloud.accessToken,
  })
}

export async function runAgentControl(
  args: string[],
  dependencies: AgentControlDependencies = {},
): Promise<AgentControlOutput> {
  const [command, target] = args
  switch (command) {
    case 'registry':
      return { ok: true, data: { model: AGENT_MODEL, tasks: TASK_REGISTRY } }
    case 'runtime-spec':
      return {
        ok: true,
        data: {
          appName: CAFFEMATE_AGENT_APP_NAME,
          classMethods: AGENT_RUNTIME_CLASS_METHODS,
        },
      }
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
    case 'gcp-preflight': {
      try {
        const result = await (dependencies.gcpPreflight ?? runDefaultGcpPreflight)(target)
        return {
          ok: result.ok,
          code: result.ok ? undefined : 'GCP_PREFLIGHT_BLOCKED',
          data: result,
        }
      } catch (error) {
        return invalid('GCP_PREFLIGHT_FAILED', error instanceof Error ? error.message : String(error))
      }
    }
    default:
      return invalid(
        'COMMAND_NOT_SUPPORTED',
        'supported commands: registry, runtime-spec, validate-fixtures, dispatch-fixture <id>, gcp-preflight [model-id]',
      )
  }
}
