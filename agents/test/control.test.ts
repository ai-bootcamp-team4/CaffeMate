import { describe, expect, it } from 'vitest'
import { runAgentControl } from '../src/control'

describe('Agent Control CLI core', () => {
  it('reports the pinned local-only registry as JSON-friendly data', async () => {
    const output = await runAgentControl(['registry'])
    expect(output.ok).toBe(true)
    expect(output.data).toMatchObject({ model: { id: 'gemini-3.7-flash', networkEnabled: false } })
  })

  it('validates every checked-in role fixture', async () => {
    const output = await runAgentControl(['validate-fixtures'])
    expect(output).toMatchObject({ ok: true, data: { total: 14, invalid: 0 } })
  })

  it('dispatches a complete fixture through the same deterministic core', async () => {
    const output = await runAgentControl(['dispatch-fixture', 'intent_delta-complete'])
    expect(output.ok).toBe(true)
    expect(output.data).toMatchObject({ task_type: 'INTENT_DELTA', status: 'COMPLETE' })
  })

  it('returns a typed failure for unknown fixture ids', async () => {
    const output = await runAgentControl(['dispatch-fixture', 'missing'])
    expect(output).toMatchObject({ ok: false, code: 'FIXTURE_NOT_FOUND' })
  })
})
