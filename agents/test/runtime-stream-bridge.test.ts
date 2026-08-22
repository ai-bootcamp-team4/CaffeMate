import { describe, expect, it, vi } from 'vitest'
import { encodeRuntimeStreamChunk, prepareRuntimeStreamMethod } from '../src/runtime-stream-bridge'

async function collect(stream: AsyncIterable<unknown>): Promise<unknown[]> {
  const values: unknown[] = []
  for await (const value of stream) values.push(value)
  return values
}

describe('Agent Runtime async stream bridge', () => {
  it('encodes every public streaming chunk in the Agent Platform output envelope', () => {
    const event = {
      author: 'DOCUMENT_ANALYST',
      content: { parts: [{ text: '{"status":"COMPLETE"}' }] },
    }

    expect(encodeRuntimeStreamChunk(event)).toBe(`${JSON.stringify({ output: event })}\n`)
  })

  it('runs async_stream_query against the exact existing managed session', async () => {
    const getSession = vi.fn(async () => ({ id: 'session-123' }))
    const runAsync = vi.fn(() => (async function* () {
      yield { author: 'INTENT_INTERPRETER', content: { parts: [{ text: '{"status":"COMPLETE"}' }] } }
    })())
    const abortController = new AbortController()

    const result = await prepareRuntimeStreamMethod(
      {
        class_method: 'async_stream_query',
        input: {
          user_id: 'p-deadbeef',
          session_id: 'session-123',
          message: '{"task_type":"INTENT_DELTA"}',
        },
      },
      { createSession: vi.fn(), getSession, deleteSession: vi.fn() },
      { runAsync },
      abortController.signal,
    )

    expect(getSession).toHaveBeenCalledWith({
      appName: 'caffemate-agents',
      userId: 'p-deadbeef',
      sessionId: 'session-123',
    })
    expect(result).toMatchObject({ handled: true, status: 200 })
    if (!result.handled || result.status !== 200) throw new Error('expected successful stream preparation')
    expect(await collect(result.stream)).toEqual([
      { author: 'INTENT_INTERPRETER', content: { parts: [{ text: '{"status":"COMPLETE"}' }] } },
    ])
    expect(runAsync).toHaveBeenCalledWith({
      userId: 'p-deadbeef',
      sessionId: 'session-123',
      newMessage: {
        role: 'user',
        parts: [{ text: '{"task_type":"INTENT_DELTA"}' }],
      },
      abortSignal: abortController.signal,
    })
  })

  it('fails closed when the requested managed session does not exist', async () => {
    const runAsync = vi.fn()
    const result = await prepareRuntimeStreamMethod(
      {
        class_method: 'async_stream_query',
        input: {
          user_id: 'p-deadbeef',
          session_id: 'missing-session',
          message: '{"task_type":"INTENT_DELTA"}',
        },
      },
      {
        createSession: vi.fn(),
        getSession: vi.fn(async () => undefined),
        deleteSession: vi.fn(),
      },
      { runAsync },
    )

    expect(result).toEqual({
      handled: true,
      status: 404,
      error: 'async_stream_query session not found',
    })
    expect(runAsync).not.toHaveBeenCalled()
  })

  it('rejects malformed stream input before runner execution', async () => {
    const getSession = vi.fn()
    const runAsync = vi.fn()

    await expect(prepareRuntimeStreamMethod(
      { class_method: 'async_stream_query', input: { user_id: 'p-deadbeef' } },
      { createSession: vi.fn(), getSession, deleteSession: vi.fn() },
      { runAsync },
    )).resolves.toEqual({
      handled: true,
      status: 400,
      error: 'async_stream_query requires input.user_id, input.session_id, and input.message',
    })
    expect(getSession).not.toHaveBeenCalled()
    expect(runAsync).not.toHaveBeenCalled()
  })

  it('leaves non-stream class methods to the unary bridge', async () => {
    const result = await prepareRuntimeStreamMethod(
      { class_method: 'async_create_session', input: { user_id: 'p-deadbeef' } },
      { createSession: vi.fn(), getSession: vi.fn(), deleteSession: vi.fn() },
      { runAsync: vi.fn() },
    )

    expect(result).toEqual({ handled: false })
  })

  it('creates, runs, and deletes one ephemeral session inside one stream', async () => {
    const createSession = vi.fn(async (input: { sessionId?: string }) => ({
      id: input.sessionId,
    }))
    const deleteSession = vi.fn(async () => undefined)
    const getSession = vi.fn()
    const runAsync = vi.fn(() => (async function* () {
      yield {
        author: 'PROPOSAL_AGENT',
        content: { parts: [{ text: '{"status":"COMPLETE"}' }] },
      }
    })())

    const result = await prepareRuntimeStreamMethod(
      {
        class_method: 'async_ephemeral_stream_query',
        input: {
          user_id: 'p-deadbeef',
          session_id: 'session-ephemeral',
          message: '{"task_type":"PROPOSE_INDEPENDENT"}',
        },
      },
      { createSession, getSession, deleteSession },
      { runAsync },
    )

    expect(result).toMatchObject({ handled: true, status: 200 })
    if (!result.handled || result.status !== 200) throw new Error('expected successful stream preparation')
    expect(deleteSession).not.toHaveBeenCalled()
    expect(await collect(result.stream)).toHaveLength(1)
    expect(createSession).toHaveBeenCalledWith({
      appName: 'caffemate-agents',
      userId: 'p-deadbeef',
      state: {},
      sessionId: 'session-ephemeral',
    })
    expect(getSession).not.toHaveBeenCalled()
    expect(deleteSession).toHaveBeenCalledWith({
      appName: 'caffemate-agents',
      userId: 'p-deadbeef',
      sessionId: 'session-ephemeral',
    })
  })

  it('deletes the ephemeral session when Agent execution fails', async () => {
    const deleteSession = vi.fn(async () => undefined)
    const result = await prepareRuntimeStreamMethod(
      {
        class_method: 'async_ephemeral_stream_query',
        input: {
          user_id: 'p-deadbeef',
          session_id: 'session-failed',
          message: '{"task_type":"CANDIDATE_AUDIT"}',
        },
      },
      {
        createSession: vi.fn(async () => ({ id: 'session-failed' })),
        getSession: vi.fn(),
        deleteSession,
      },
      {
        runAsync: vi.fn(() => (async function* () {
          throw new Error('model failed')
          yield undefined
        })()),
      },
    )

    if (!result.handled || result.status !== 200) throw new Error('expected prepared stream')
    await expect(collect(result.stream)).rejects.toThrow('model failed')
    expect(deleteSession).toHaveBeenCalledOnce()
  })

  it('surfaces an explicit cleanup code when ephemeral deletion is uncertain', async () => {
    const result = await prepareRuntimeStreamMethod(
      {
        class_method: 'async_ephemeral_stream_query',
        input: {
          user_id: 'p-deadbeef',
          session_id: 'session-cleanup-failed',
          message: '{"task_type":"EVIDENCE_ASSESS"}',
        },
      },
      {
        createSession: vi.fn(async () => ({ id: 'session-cleanup-failed' })),
        getSession: vi.fn(),
        deleteSession: vi.fn(async () => { throw new Error('delete unavailable') }),
      },
      {
        runAsync: vi.fn(() => (async function* () {
          yield { author: 'EVIDENCE_RESEARCHER' }
        })()),
      },
    )

    if (!result.handled || result.status !== 200) throw new Error('expected prepared stream')
    await expect(collect(result.stream)).rejects.toMatchObject({
      code: 'RUNTIME_SESSION_CLEANUP_FAILED',
      name: 'RuntimeSessionCleanupError',
    })
  })
})
