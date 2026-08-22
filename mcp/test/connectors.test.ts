import { describe, expect, it, vi } from 'vitest'
import { createConnectorRegistry, JUSO_SOURCE_ID } from '../src/connectors'

const scope = { ventureProjectId: 'project-1', workflowRunId: 'workflow-1', requestId: 'request-1' }
const now = new Date('2026-08-22T01:00:00Z')

describe('official address connectors', () => {
  it('abstains explicitly when the official API credential is absent', async () => {
    const connector = createConnectorRegistry({ now: () => now }).resolve_area!
    const result = await connector({ query: '수원 아주대', country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>
    expect(result).toMatchObject({ status: 'PARTIAL', data: [], missing_fields: ['JUSO_API_KEY'], error_codes: ['SOURCE_CREDENTIAL_MISSING'] })
  })

  it('maps official Juso results to deduplicated administrative areas', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      results: { common: { errorCode: '0' }, juso: [
        { admCd: '4111710300', siNm: '경기도', sggNm: '수원시 영통구', emdNm: '원천동' },
        { admCd: '4111710300', siNm: '경기도', sggNm: '수원시 영통구', emdNm: '원천동' },
      ] },
    }), { status: 200 }))
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now }).resolve_area!
    const result = await connector({ query: '수원 원천동', country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>
    expect(result).toMatchObject({ status: 'OK', project_id: 'project-1' })
    expect(result.data).toEqual([{
      administrative_code: '4111710300', display_name: '경기도 수원시 영통구 원천동',
      boundary_version: '2026-08-22', match_kind: 'AMBIGUOUS',
    }])
    expect(fetcher).toHaveBeenCalledOnce()
  })

  it('reports configured and unknown source health without inventing success timestamps', async () => {
    const fetcher = vi.fn(async () => new Response('official guide', { status: 200 }))
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now }).get_source_health!
    const result = await connector({ source_ids: [JUSO_SOURCE_ID, 'unknown-source'], as_of: '2026-08-22' }, scope) as Record<string, unknown>
    expect(result.status).toBe('PARTIAL')
    expect(result.data).toEqual([
      { source_id: JUSO_SOURCE_ID, status: 'HEALTHY', last_success_at: '2026-08-22T01:00:00.000Z', data_date: null },
      { source_id: 'unknown-source', status: 'UNAVAILABLE', last_success_at: null, data_date: null },
    ])
    expect(result.source_trace).toHaveLength(1)
  })
})
