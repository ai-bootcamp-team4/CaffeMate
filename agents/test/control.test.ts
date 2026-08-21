import { describe, expect, it, vi } from 'vitest'
import { runAgentControl } from '../src/control'

describe('Agent Control CLI core', () => {
  it('reports the pinned global generation registry as JSON-friendly data', async () => {
    const output = await runAgentControl(['registry'])
    expect(output.ok).toBe(true)
    expect(output.data).toMatchObject({
      model: {
        id: 'gemini-3.7-flash',
        approvalStatus: 'APPROVED',
        region: 'global',
        networkEnabled: true,
      },
    })
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

  it('runs the GCP preflight through the Agent Control CLI and forwards an optional model candidate', async () => {
    const gcpPreflight = vi.fn(async (modelId?: string) => ({
      ok: false,
      projectId: 'proj-aj20-211200020328',
      runtimeRegion: 'asia-northeast3' as const,
      generationRegion: 'global' as const,
      ragRegion: 'asia-northeast3' as const,
      embeddingRegion: 'asia-northeast3' as const,
      checks: [{ name: 'generation-model' as const, ok: false, code: 'MODEL_NOT_APPROVED' }],
      modelId,
    }))

    const output = await runAgentControl(
      ['gcp-preflight', 'gemini-3.7-flash'],
      { gcpPreflight },
    )

    expect(gcpPreflight).toHaveBeenCalledWith('gemini-3.7-flash')
    expect(output).toMatchObject({
      ok: false,
      code: 'GCP_PREFLIGHT_BLOCKED',
      data: { projectId: 'proj-aj20-211200020328' },
    })
  })
})
