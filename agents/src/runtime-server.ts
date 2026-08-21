import { VertexAiSessionService } from '@google/adk'
import { AdkApiServer } from '@google/adk-devtools'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { GCP_LOCATIONS } from './registry'
import {
  handleRuntimeClassMethod,
  type RuntimeClassMethodRequest,
} from './runtime-session-bridge'

const DEFAULT_PORT = 8080
const MAX_CLASS_METHOD_BODY_BYTES = 1024 * 1024

function nonEmptyString(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

async function readJsonBody(request: AsyncIterable<unknown>): Promise<RuntimeClassMethodRequest> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk))
    size += buffer.length
    if (size > MAX_CLASS_METHOD_BODY_BYTES) throw new Error('request body exceeds 1 MiB')
    chunks.push(buffer)
  }
  const text = Buffer.concat(chunks).toString('utf8')
  if (!text) throw new Error('request body is required')
  const parsed: unknown = JSON.parse(text)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('request body must be a JSON object')
  }
  return parsed as RuntimeClassMethodRequest
}

interface RuntimeEnvironment {
  projectId: string
  runtimeRegion: typeof GCP_LOCATIONS.runtime
  agentEngineId: string
  port: number
}

type RuntimeRequest = AsyncIterable<unknown>

interface RuntimeResponse {
  status(code: number): RuntimeResponse
  json(body: unknown): unknown
}

export function runtimeEnvironmentFrom(
  env: NodeJS.ProcessEnv = process.env,
): RuntimeEnvironment {
  const projectId = nonEmptyString(env.GOOGLE_CLOUD_PROJECT)
  const runtimeRegion = nonEmptyString(
    env.GOOGLE_CLOUD_AGENT_ENGINE_LOCATION ?? env.GOOGLE_CLOUD_LOCATION,
  )
  const agentEngineId = nonEmptyString(env.GOOGLE_CLOUD_AGENT_ENGINE_ID)
  const port = Number(env.PORT ?? DEFAULT_PORT)

  if (!projectId) throw new Error('RUNTIME_PROJECT_UNRESOLVED: GOOGLE_CLOUD_PROJECT is required')
  if (runtimeRegion !== GCP_LOCATIONS.runtime) {
    throw new Error(`RUNTIME_REGION_MISMATCH: expected ${GCP_LOCATIONS.runtime}, got ${runtimeRegion ?? 'missing'}`)
  }
  if (!agentEngineId) throw new Error('RUNTIME_ENGINE_ID_UNRESOLVED: GOOGLE_CLOUD_AGENT_ENGINE_ID is required')
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error(`RUNTIME_PORT_INVALID: ${String(env.PORT)}`)
  }

  return { projectId, runtimeRegion, agentEngineId, port }
}

export async function startCaffeMateRuntimeServer(): Promise<AdkApiServer> {
  const runtime = runtimeEnvironmentFrom()
  const sessionService = new VertexAiSessionService({
    projectId: runtime.projectId,
    location: runtime.runtimeRegion,
    agentEngineId: runtime.agentEngineId,
  })
  const server = new AdkApiServer({
    agentsDir: process.env.CAFFEMATE_AGENTS_DIR ?? path.resolve(process.cwd(), 'agents'),
    host: '0.0.0.0',
    port: runtime.port,
    sessionService,
    serveDebugUI: false,
  })

  // Register before AdkApiServer.start(). Its built-in Reasoning Engine route in
  // ADK JS 1.6.0 treats every class method as an agent run; CaffeMate requires
  // managed create/delete session methods to share the same Vertex session
  // service used by /run.
  server.app.post('/api/reasoning_engine', async (req: RuntimeRequest, res: RuntimeResponse) => {
    let body: RuntimeClassMethodRequest
    try {
      body = await readJsonBody(req)
    } catch (error) {
      res.status(400).json({
        error: error instanceof Error ? error.message : String(error),
      })
      return
    }

    try {
      const result = await handleRuntimeClassMethod(body, sessionService)
      if (!result.handled) {
        res.status(400).json({ error: 'unsupported class_method' })
        return
      }
      res.status(result.status).json(result.body)
    } catch (error) {
      res.status(500).json({
        error: error instanceof Error ? error.message : String(error),
      })
    }
  })

  await server.start()
  return server
}

const invokedAsEntrypoint = process.argv[1]
  ? import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
  : false

if (invokedAsEntrypoint) {
  startCaffeMateRuntimeServer().catch((error) => {
    console.error(error)
    process.exitCode = 1
  })
}