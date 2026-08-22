import { CAFFEMATE_AGENT_APP_NAME } from './runtime-contract'

export interface RuntimeStreamMethodRequest {
  class_method?: unknown
  input?: unknown
}

interface RuntimeStreamSessionService {
  getSession(input: {
    appName: string
    userId: string
    sessionId: string
  }): Promise<unknown>
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
  | { handled: true; status: 400 | 404; error: string }
  | { handled: true; status: 200; stream: AsyncIterable<unknown> }

function inputObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function requiredString(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  return value
}

export async function prepareRuntimeStreamMethod(
  request: RuntimeStreamMethodRequest,
  sessionService: RuntimeStreamSessionService,
  runner: RuntimeStreamRunner,
  abortSignal?: AbortSignal,
): Promise<RuntimeStreamMethodResult> {
  if (request.class_method !== 'async_stream_query') return { handled: false }

  const input = inputObject(request.input)
  const userId = requiredString(input?.user_id)
  const sessionId = requiredString(input?.session_id)
  const message = requiredString(input?.message)
  if (!userId || !sessionId || !message) {
    return {
      handled: true,
      status: 400,
      error: 'async_stream_query requires input.user_id, input.session_id, and input.message',
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