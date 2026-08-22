import { CAFFEMATE_AGENT_APP_NAME } from './runtime-contract'

export interface ManagedSessionService {
  createSession(request: {
    appName: string
    userId: string
    state?: Record<string, unknown>
    sessionId?: string
  }): Promise<unknown>
  deleteSession(request: {
    appName: string
    userId: string
    sessionId: string
  }): Promise<void>
}

export interface RuntimeClassMethodRequest {
  class_method?: unknown
  input?: unknown
}

export type RuntimeClassMethodResult =
  | { handled: false }
  | { handled: true; status: number; body: { output?: unknown; error?: string } }

function nonEmptyString(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

function inputObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

export async function handleRuntimeClassMethod(
  request: RuntimeClassMethodRequest,
  sessionService: ManagedSessionService,
): Promise<RuntimeClassMethodResult> {
  const classMethod = nonEmptyString(request.class_method)
  if (classMethod !== 'async_create_session' && classMethod !== 'async_delete_session') {
    return { handled: false }
  }

  const input = inputObject(request.input)
  const userId = nonEmptyString(input?.user_id)
  if (!input || !userId) {
    return {
      handled: true,
      status: 400,
      body: { error: `${classMethod} requires input.user_id` },
    }
  }

  if (classMethod === 'async_create_session') {
    const sessionId = nonEmptyString(input.session_id)
    if (!sessionId) {
      return {
        handled: true,
        status: 400,
        body: { error: 'async_create_session requires input.session_id' },
      }
    }
    const state = input.state === undefined ? {} : inputObject(input.state)
    if (!state) {
      return {
        handled: true,
        status: 400,
        body: { error: 'async_create_session input.state must be an object when provided' },
      }
    }
    const session = await sessionService.createSession({
      appName: CAFFEMATE_AGENT_APP_NAME,
      userId,
      state,
      sessionId,
    })
    if (!session || typeof session !== 'object' || (session as { id?: unknown }).id !== sessionId) {
      return {
        handled: true,
        status: 502,
        body: { error: 'managed session service returned a mismatched session id' },
      }
    }
    return { handled: true, status: 200, body: { output: session } }
  }

  const sessionId = nonEmptyString(input.session_id)
  if (!sessionId) {
    return {
      handled: true,
      status: 400,
      body: { error: 'async_delete_session requires input.session_id' },
    }
  }
  await sessionService.deleteSession({
    appName: CAFFEMATE_AGENT_APP_NAME,
    userId,
    sessionId,
  })
  return { handled: true, status: 200, body: { output: null } }
}
