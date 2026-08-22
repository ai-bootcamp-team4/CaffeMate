import { AGENT_RUNTIME_CLASS_METHODS } from './runtime-contract'

export interface PreflightCheck<Name extends string> {
  name: Name
  ok: boolean
  code: string
  detail?: string
}

export interface GcpRuntimePin {
  resourceName: string
  imageUri: string
  promptBundleDigest: string
  agentContractBundleDigest: string
}

export interface GcpMcpRuntimePin {
  serviceName: string
  region: string
  sourceRevision: string
  imageUri: string
}

interface ReasoningEngineRow {
  name?: string
  displayName?: string
  spec?: {
    classMethods?: Array<{ name?: string; api_mode?: string }>
    containerSpec?: { imageUri?: string }
  }
}

interface ReasoningEngineIdentity {
  project: string
  location: string
  resourceId: string
}

interface CloudRunServiceRow {
  name?: string
  template?: {
    labels?: Record<string, string>
    containers?: Array<{ image?: string }>
  }
}

const RUNTIME_DISPLAY_NAME = 'caffemate-agents'
const GIT_REVISION = /^[0-9a-f]{40}$/
const DIGEST_IMAGE = /^[a-z0-9.-]+\/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$/

function pass<Name extends string>(name: Name, code: string, detail?: string): PreflightCheck<Name> {
  return { name, ok: true, code, ...(detail ? { detail } : {}) }
}

function fail<Name extends string>(name: Name, code: string, detail?: string): PreflightCheck<Name> {
  return { name, ok: false, code, ...(detail ? { detail } : {}) }
}

async function request(fetchImpl: typeof fetch, token: string, url: string, init?: RequestInit): Promise<Response> {
  return fetchImpl(url, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
}

function reasoningEngineIdentity(resourceName: string): ReasoningEngineIdentity | null {
  const match = /^projects\/([^/]+)\/locations\/([^/]+)\/reasoningEngines\/([^/]+)$/.exec(resourceName)
  if (!match) return null
  const [, project, location, resourceId] = match
  if (!project || !location || !resourceId) return null
  return { project, location, resourceId }
}

function runtimeClassMethodMismatch(runtime: ReasoningEngineRow): string | null {
  const actual = new Set(
    (runtime.spec?.classMethods ?? [])
      .filter((method) => typeof method.name === 'string' && typeof method.api_mode === 'string')
      .map((method) => `${method.name}:${method.api_mode}`),
  )
  const expected = AGENT_RUNTIME_CLASS_METHODS.map((method) => `${method.name}:${method.api_mode}`)
  const expectedSet = new Set<string>(expected)
  for (const method of expected) {
    if (!actual.has(method)) return method
  }
  if (actual.size !== expected.length) {
    return [...actual].find((method) => !expectedSet.has(method)) ?? 'unexpected class method'
  }
  return null
}

export function validateRuntimePin(projectId: string, region: string, pin: GcpRuntimePin): boolean {
  const runtime = reasoningEngineIdentity(pin.resourceName)
  return Boolean(runtime && runtime.project === projectId && runtime.location === region && DIGEST_IMAGE.test(pin.imageUri))
}

export interface AgentRuntimePreflightResult {
  check: PreflightCheck<'agent-runtime'>
  runtimeResource?: string
  runtimeImageUri?: string
}

export async function verifyAgentRuntime(options: {
  projectId: string
  region: string
  pin: GcpRuntimePin
  token: string
  fetchImpl: typeof fetch
}): Promise<AgentRuntimePreflightResult> {
  const pinnedRuntime = reasoningEngineIdentity(options.pin.resourceName)
  if (!pinnedRuntime) {
    return { check: fail('agent-runtime', 'AGENT_RUNTIME_PIN_INVALID', options.pin.resourceName) }
  }
  const base = `https://${options.region}-aiplatform.googleapis.com/v1/projects/${options.projectId}/locations/${options.region}`
  const response = await request(
    options.fetchImpl,
    options.token,
    `${base}/reasoningEngines/${encodeURIComponent(pinnedRuntime.resourceId)}`,
  )
  if (!response.ok) {
    return { check: fail('agent-runtime', 'AGENT_RUNTIME_GET_FAILED', `HTTP ${response.status}`) }
  }

  const runtime = await response.json() as ReasoningEngineRow
  const runtimeResource = runtime.name
  const runtimeImageUri = runtime.spec?.containerSpec?.imageUri
  const actualIdentity = runtime.name ? reasoningEngineIdentity(runtime.name) : null
  const mismatch = runtimeClassMethodMismatch(runtime)
  let check: PreflightCheck<'agent-runtime'>
  if (!actualIdentity
    || actualIdentity.location !== pinnedRuntime.location
    || actualIdentity.resourceId !== pinnedRuntime.resourceId) {
    check = fail('agent-runtime', 'AGENT_RUNTIME_RESOURCE_MISMATCH', runtime.name ?? 'MISSING')
  } else if (runtime.displayName !== RUNTIME_DISPLAY_NAME) {
    check = fail('agent-runtime', 'AGENT_RUNTIME_DISPLAY_NAME_MISMATCH', runtime.displayName ?? 'MISSING')
  } else if (mismatch) {
    check = fail('agent-runtime', 'AGENT_RUNTIME_CLASS_METHOD_MISMATCH', mismatch)
  } else if (runtimeImageUri !== options.pin.imageUri) {
    check = fail('agent-runtime', 'AGENT_RUNTIME_IMAGE_MISMATCH', runtimeImageUri ?? 'MISSING')
  } else {
    const identityResponse = await request(
      options.fetchImpl,
      options.token,
      `${base}/reasoningEngines/${encodeURIComponent(pinnedRuntime.resourceId)}:query`,
      {
        method: 'POST',
        body: JSON.stringify({ class_method: 'async_get_release_identity', input: {} }),
      },
    )
    if (!identityResponse.ok) {
      check = fail('agent-runtime', 'AGENT_RUNTIME_RELEASE_IDENTITY_FAILED', `HTTP ${identityResponse.status}`)
    } else {
      const payload = await identityResponse.json() as {
        output?: {
          schema_version?: string
          prompt_bundle_digest?: string
          agent_contract_bundle_digest?: string
        }
      }
      const identity = payload.output
      check = identity?.schema_version === '1.0.0'
        && identity.prompt_bundle_digest === options.pin.promptBundleDigest
        && identity.agent_contract_bundle_digest === options.pin.agentContractBundleDigest
        ? pass('agent-runtime', 'AGENT_RUNTIME_OK', runtimeResource)
        : fail(
            'agent-runtime',
            'AGENT_RUNTIME_RELEASE_IDENTITY_MISMATCH',
            `${identity?.prompt_bundle_digest ?? 'MISSING'} ${identity?.agent_contract_bundle_digest ?? 'MISSING'}`,
          )
    }
  }

  return {
    check,
    ...(runtimeResource ? { runtimeResource } : {}),
    ...(runtimeImageUri ? { runtimeImageUri } : {}),
  }
}

export function validateMcpRuntimePin(projectId: string, region: string, pin: GcpMcpRuntimePin): boolean {
  return pin.serviceName === 'caffemate-mcp'
    && pin.region === region
    && Boolean(projectId)
    && GIT_REVISION.test(pin.sourceRevision)
    && DIGEST_IMAGE.test(pin.imageUri)
}

export async function verifyMcpRuntime(options: {
  projectId: string
  pin: GcpMcpRuntimePin
  token: string
  fetchImpl: typeof fetch
}): Promise<PreflightCheck<'mcp-runtime'>> {
  const expectedName = `projects/${options.projectId}/locations/${options.pin.region}/services/${options.pin.serviceName}`
  const response = await request(
    options.fetchImpl,
    options.token,
    `https://run.googleapis.com/v2/${expectedName}`,
  )
  if (!response.ok) return fail('mcp-runtime', 'MCP_RUNTIME_GET_FAILED', `HTTP ${response.status}`)

  const service = await response.json() as CloudRunServiceRow
  if (service.name !== expectedName) {
    return fail('mcp-runtime', 'MCP_RUNTIME_RESOURCE_MISMATCH', service.name ?? 'MISSING')
  }
  const sourceRevision = service.template?.labels?.['source-revision']
  if (sourceRevision !== options.pin.sourceRevision) {
    return fail('mcp-runtime', 'MCP_RUNTIME_SOURCE_REVISION_MISMATCH', sourceRevision ?? 'MISSING')
  }
  const containers = service.template?.containers
  if (!Array.isArray(containers) || containers.length !== 1 || containers[0]?.image !== options.pin.imageUri) {
    return fail('mcp-runtime', 'MCP_RUNTIME_IMAGE_MISMATCH', containers?.[0]?.image ?? 'MISSING')
  }
  return pass('mcp-runtime', 'MCP_RUNTIME_OK', options.pin.imageUri)
}
