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
    const fetcher = vi.fn(async (input: unknown) => {
      void input
      return new Response(JSON.stringify({
        results: { common: { errorCode: '0' }, juso: [
          { admCd: '4111710300', siNm: '경기도', sggNm: '수원시 영통구', emdNm: '원천동' },
          { admCd: '4111710300', siNm: '경기도', sggNm: '수원시 영통구', emdNm: '원천동' },
        ] },
      }), { status: 200 })
    })
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now }).resolve_area!
    const result = await connector({ query: '수원 원천동', country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>
    expect(result).toMatchObject({ status: 'OK', project_id: 'project-1' })
    expect(result.data).toEqual([{
      administrative_code: '4111710300', display_name: '경기도 수원시 영통구 원천동',
      boundary_version: 'JUSO_LIVE_UNVERSIONED', match_kind: 'CONTAINS',
    }])
    expect(fetcher).toHaveBeenCalledOnce()
    expect(new URL(String(fetcher.mock.calls[0]?.[0])).searchParams.get('countPerPage')).toBe('5')
  })

  it.each([
    ['성수', '성수동', '1120011500', '서울특별시', '성동구', '성수동1가'],
    ['망원', '망원동', '1144012300', '서울특별시', '마포구', '망원동'],
    ['조원', '조원동', '4111113000', '경기도', '수원시 장안구', '조원동'],
    ['원천', '원천동', '4111710300', '경기도', '수원시 영통구', '원천동'],
  ])('resolves the short locality %s without a place-specific alias table', async (
    query,
    expandedQuery,
    admCd,
    siNm,
    sggNm,
    emdNm,
  ) => {
    const fetcher = vi.fn(async (input: unknown) => {
      const keyword = new URL(String(input)).searchParams.get('keyword')
      const juso = keyword === expandedQuery
        ? [{ admCd, siNm, sggNm, emdNm }]
        : keyword === query
          ? [{
              admCd: '1168010700', siNm: '서울특별시', sggNm: '강남구', emdNm: '신사동',
              roadAddr: `서울특별시 강남구 ${query}로 1`,
            }]
          : []
      return new Response(JSON.stringify({ results: { common: { errorCode: '0' }, juso } }), { status: 200 })
    })
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now }).resolve_area!

    const result = await connector({ query, country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>

    expect(result).toMatchObject({ status: 'OK' })
    expect(result.data).toEqual([{
      administrative_code: admCd,
      display_name: `${siNm} ${sggNm} ${emdNm}`,
      boundary_version: 'JUSO_LIVE_UNVERSIONED',
      match_kind: 'ALIAS',
    }])
  })

  it('filters unrelated address hits and ranks every locality token for composite input', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      results: { common: { errorCode: '0' }, juso: [
        { admCd: '4111710500', siNm: '경기도', sggNm: '수원시 영통구', emdNm: '영통동' },
        { admCd: '1168010700', siNm: '서울특별시', sggNm: '강남구', emdNm: '신사동', roadAddr: '서울특별시 강남구 수원영통로 1' },
      ] },
    }), { status: 200 }))
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now }).resolve_area!

    const result = await connector({ query: '수원 영통구', country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>

    expect(result.data).toEqual([{
      administrative_code: '4111710500',
      display_name: '경기도 수원시 영통구 영통동',
      boundary_version: 'JUSO_LIVE_UNVERSIONED',
      match_kind: 'CONTAINS',
    }])
    expect(fetcher).toHaveBeenCalledOnce()
  })

  it('retries one transient official API failure before returning candidates', async () => {
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        results: { common: { errorCode: '0' }, juso: [
          { admCd: '1144012300', siNm: '서울특별시', sggNm: '마포구', emdNm: '망원동' },
        ] },
      }), { status: 200 }))
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now }).resolve_area!

    const result = await connector({ query: '망원동', country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>

    expect(result).toMatchObject({ status: 'OK' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('reports configured and unknown source health without inventing success timestamps', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ results: { common: { errorCode: '0' }, juso: [] } }), { status: 200 }))
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now }).get_source_health!
    const result = await connector({ source_ids: [JUSO_SOURCE_ID, 'unknown-source'], as_of: '2026-08-22' }, scope) as Record<string, unknown>
    expect(result.status).toBe('PARTIAL')
    expect(result.data).toEqual([
      { source_id: JUSO_SOURCE_ID, status: 'HEALTHY', last_success_at: '2026-08-22T01:00:00.000Z', data_date: null },
      { source_id: 'unknown-source', status: 'UNAVAILABLE', last_success_at: null, data_date: null },
    ])
    expect(result.source_trace).toHaveLength(1)
  })

  it('does not report a configured but rejected credential as healthy', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ results: { common: { errorCode: 'E0005' } } }), { status: 200 }))
    const connector = createConnectorRegistry({ jusoApiKey: 'rejected', fetch: fetcher as typeof fetch, now: () => now }).get_source_health!
    const result = await connector({ source_ids: [JUSO_SOURCE_ID], as_of: '2026-08-22' }, scope) as Record<string, unknown>
    expect(result).toMatchObject({
      status: 'PARTIAL',
      data: [{ source_id: JUSO_SOURCE_ID, status: 'UNAVAILABLE', last_success_at: null, data_date: null }],
    })
  })
})
