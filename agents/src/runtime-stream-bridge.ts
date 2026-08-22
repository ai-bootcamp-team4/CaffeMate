import { CAFFEMATE_AGENT_APP_NAME } from './runtime-contract'

export interface RuntimeStreamMethodRequest {
  class_method?: unknown
  input?: unknown
}

interface RuntimeStreamSessionService {
  createSession(input: {
    appName: string
    userId: string
    state?: Record<string, unknown>
    sessionId?: string
  }): Promise<unknown>
  getSession(input: {
    appName: string
    userId: string
    sessionId: string
  }): Promise<unknown>
  deleteSession(input: {
    appName: string
    userId: string
    sessionId: string
  }): Promise<void>
}

interface RuntimeStreamRunner {
  runAsync(input: {
    userId: string
    sessionId: string
    newMessage: {
      role: 'user'
      parts: [{ text: string }]
    }
    abortSignal?: AbortSignal
  }): AsyncIterable<unknown>
}

export type RuntimeStreamMethodResult =
  | { handled: false }
  | { handled: true; status: 400 | 404 | 502; error: string }
  | { handled: true; status: 200; stream: AsyncIterable<unknown> }

export function encodeRuntimeStreamChunk(chunk: unknown): string {
  return `${JSON.stringify({ output: chunk })}\n`
}

function inputObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function requiredString(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  return value
}

async function* runEphemeralSession(
  sessionService: RuntimeStreamSessionService,
  runner: RuntimeStreamRunner,
  input: {
    userId: string
    sessionId: string
    message: string
    abortSignal?: AbortSignal
  },
): AsyncIterable<unknown> {
  let cleanupError: Error | null = null
  try {
    for await (const event of runner.runAsync({
      userId: input.userId,
      sessionId: input.sessionId,
      newMessage: {
        role: 'user',
        parts: [{ text: input.message }],
      },
      ...(input.abortSignal ? { abortSignal: input.abortSignal } : {}),
    })) {
      yield event
    }
  } finally {
    try {
      await sessionService.deleteSession({
        appName: CAFFEMATE_AGENT_APP_NAME,
        userId: input.userId,
        sessionId: input.sessionId,
      })
    } catch (cause) {
      const error = new Error('ephemeral managed session deletion failed', { cause })
      error.name = 'RuntimeSessionCleanupError'
      Object.assign(error, { code: 'RUNTIME_SESSION_CLEANUP_FAILED' })
      cleanupError = error
    }
  }
  if (cleanupError) throw cleanupError
}

export async function prepareRuntimeStreamMethod(
  request: RuntimeStreamMethodRequest,
  sessionService: RuntimeStreamSessionService,
  runner: RuntimeStreamRunner,
  abortSignal?: AbortSignal,
): Promise<RuntimeStreamMethodResult> {
  if (request.class_method !== 'async_stream_query'
    && request.class_method !== 'async_ephemeral_stream_query') return { handled: false }

  const input = inputObject(request.input)
  const userId = requiredString(input?.user_id)
  const sessionId = requiredString(input?.session_id)
  const message = requiredString(input?.message)
  if (!userId || !sessionId || !message) {
    return {
      handled: true,
      status: 400,
      error: `${String(request.class_method)} requires input.user_id, input.session_id, and input.message`,
    }
  }

  if (request.class_method === 'async_ephemeral_stream_query') {
    const session = await sessionService.createSession({
      appName: CAFFEMATE_AGENT_APP_NAME,
      userId,
      state: {},
      sessionId,
    })
    if (!session || typeof session !== 'object'
      || (session as { id?: unknown }).id !== sessionId) {
      return {
        handled: true,
        status: 502,
        error: 'managed session service returned a mismatched session id',
      }
    }
    return {
      handled: true,
      status: 200,
      stream: runEphemeralSession(sessionService, runner, {
        userId,
        sessionId,
        message,
        ...(abortSignal ? { abortSignal } : {}),
      }),
    }
  }

  const session = await sessionService.getSession({
    appName: CAFFEMATE_AGENT_APP_NAME,
    userId,
    sessionId,
  })
  if (!session) {
    return {
      handled: true,
      status: 404,
      error: 'async_stream_query session not found',
    }
  }

  return {
    handled: true,
    status: 200,
    stream: runner.runAsync({
      userId,
      sessionId,
      newMessage: {
        role: 'user',
        parts: [{ text: message }],
      },
      ...(abortSignal ? { abortSignal } : {}),
    }),
  }
}
