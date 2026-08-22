import { describe, expect, it, vi } from 'vitest'
import { bindRuntimeStreamAbort } from '../src/runtime-abort'
import { computeAgentContractBundleDigest, computePromptBundleDigest } from '../src/release-seal'
import { handleRuntimeClassMethod } from '../src/runtime-session-bridge'

describe('Agent Runtime stream cancellation', () => {
  it('does not treat a normally consumed request body as a client disconnect', () => {
    const requestListeners = new Map<string, () => void>()
    const responseListeners = new Map<string, () => void>()
    let completed = false
    const request = {
      on: vi.fn((event: string, listener: () => void) => requestListeners.set(event, listener)),
    }
    const response = {
      on: vi.fn((event: string, listener: () => void) => responseListeners.set(event, listener)),
    }

    const controller = bindRuntimeStreamAbort(
      request as never,
      response as never,
      () => completed,
    )

    expect(requestListeners.has('aborted')).toBe(true)
    expect(requestListeners.has('close')).toBe(false)
    expect(controller.signal.aborted).toBe(false)

    completed = true
    responseListeners.get('close')?.()
    expect(controller.signal.aborted).toBe(false)
  })

  it('aborts an unfinished stream when the client disconnects', () => {
    const requestListeners = new Map<string, () => void>()
    const responseListeners = new Map<string, () => void>()
    const controller = bindRuntimeStreamAbort(
      { on: (event: string, listener: () => void) => requestListeners.set(event, listener) } as never,
      { on: (event: string, listener: () => void) => responseListeners.set(event, listener) } as never,
      () => false,
    )

    requestListeners.get('aborted')?.()

    expect(controller.signal.aborted).toBe(true)
  })
})

describe('Agent Runtime managed-session bridge', () => {
  it('creates a managed session for async_create_session', async () => {
    const createSession = vi.fn(async () => ({
      id: 'session-123',
      appName: 'caffemate-agents',
      userId: 'p-deadbeef',
      state: {},
      events: [],
      lastUpdateTime: 1,
    }))

    const result = await handleRuntimeClassMethod(
      {
        class_method: 'async_create_session',
        input: { user_id: 'p-deadbeef', session_id: 'session-123' },
      },
      { createSession, deleteSession: vi.fn() },
    )

    expect(createSession).toHaveBeenCalledWith({
      appName: 'caffemate-agents',
      userId: 'p-deadbeef',
      state: {},
      sessionId: 'session-123',
    })
    expect(result).toEqual({
      handled: true,
      status: 200,
      body: {
        output: expect.objectContaining({
          id: 'session-123',
          userId: 'p-deadbeef',
        }),
      },
    })
  })

  it('fails closed when the managed service returns a different session id', async () => {
    const createSession = vi.fn(async () => ({ id: 'different-session' }))

    const result = await handleRuntimeClassMethod(
      {
        class_method: 'async_create_session',
        input: { user_id: 'p-deadbeef', session_id: 'session-123' },
      },
      { createSession, deleteSession: vi.fn() },
    )

    expect(result).toEqual({
      handled: true,
      status: 502,
      body: { error: 'managed session service returned a mismatched session id' },
    })
  })

  it('deletes the same managed session for async_delete_session', async () => {
    const deleteSession = vi.fn(async () => undefined)

    const result = await handleRuntimeClassMethod(
      {
        class_method: 'async_delete_session',
        input: { user_id: 'p-deadbeef', session_id: 'session-123' },
      },
      { createSession: vi.fn(), deleteSession },
    )

    expect(deleteSession).toHaveBeenCalledWith({
      appName: 'caffemate-agents',
      userId: 'p-deadbeef',
      sessionId: 'session-123',
    })
    expect(result).toEqual({ handled: true, status: 200, body: { output: null } })
  })

  it('reads back the release identity computed from the deployed Runtime artifact', async () => {
    const service = { createSession: vi.fn(), deleteSession: vi.fn() }

    const result = await handleRuntimeClassMethod(
      { class_method: 'async_get_release_identity', input: {} },
      service,
    )

    expect(service.createSession).not.toHaveBeenCalled()
    expect(service.deleteSession).not.toHaveBeenCalled()
    expect(result).toEqual({
      handled: true,
      status: 200,
      body: {
        output: {
          schema_version: '1.0.0',
          prompt_bundle_digest: computePromptBundleDigest(),
          agent_contract_bundle_digest: computeAgentContractBundleDigest(),
        },
      },
    })
  })

  it('fails closed on malformed managed-session requests', async () => {
    const service = { createSession: vi.fn(), deleteSession: vi.fn() }

    await expect(handleRuntimeClassMethod(
      { class_method: 'async_create_session', input: {} },
      service,
    )).resolves.toMatchObject({ handled: true, status: 400 })

    await expect(handleRuntimeClassMethod(
      { class_method: 'async_delete_session', input: { user_id: 'p-deadbeef' } },
      service,
    )).resolves.toMatchObject({ handled: true, status: 400 })

    expect(service.createSession).not.toHaveBeenCalled()
    expect(service.deleteSession).not.toHaveBeenCalled()
  })

  it('leaves unrelated class methods to the ADK API server', async () => {
    const result = await handleRuntimeClassMethod(
      { class_method: 'async_stream_query', input: {} },
      { createSession: vi.fn(), deleteSession: vi.fn() },
    )

    expect(result).toEqual({ handled: false })
  })
})
