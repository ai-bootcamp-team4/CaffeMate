import { describe, expect, it, vi } from 'vitest'
import { createConnectorRegistry, JUSO_SOURCE_ID, LEGAL_DONG_SOURCE_ID } from '../src/connectors'

const scope = { ventureProjectId: 'project-1', workflowRunId: 'workflow-1', requestId: 'request-1' }
const now = new Date('2026-08-22T01:00:00Z')

describe('official address connectors', () => {
  it.each([
    ['성수', ['1120011400', '1120011500'], ['서울특별시 성동구 성수동1가', '서울특별시 성동구 성수동2가']],
    ['망원동', ['1144012300'], ['서울특별시 마포구 망원동']],
    ['경기도 수원시 영통구 원천동', ['4111710200'], ['경기도 수원시 영통구 원천동']],
    ['조원동', ['4111113600'], ['경기도 수원시 장안구 조원동']],
    ['연무동', ['4111113700'], ['경기도 수원시 장안구 연무동']],
  ])('resolves %s from the versioned official directory without a network call', async (
    query,
    expectedCodes,
    expectedNames,
  ) => {
    const fetcher = vi.fn()
    const connector = createConnectorRegistry({
      jusoApiKey: 'configured',
      fetch: fetcher as typeof fetch,
      now: () => now,
    }).resolve_area!

    const result = await connector({ query, country_code: 'KR', limit: 10 }, scope) as {
      status: string
      data: Array<{ administrative_code: string; display_name: string; boundary_version: string }>
      source_trace: Array<{ source_id: string; data_date: string }>
    }

    expect(result.status).toBe('OK')
    expect(result.data.slice(0, expectedCodes.length).map((candidate) => candidate.administrative_code)).toEqual(expectedCodes)
    expect(result.data.slice(0, expectedNames.length).map((candidate) => candidate.display_name)).toEqual(expectedNames)
    expect(result.data.every((candidate) => candidate.boundary_version === 'MOIS_LEGAL_DONG_20260301')).toBe(true)
    expect(result.source_trace).toEqual([expect.objectContaining({
      source_id: LEGAL_DONG_SOURCE_ID,
      data_date: '2026-03-01',
    })])
    expect(fetcher).not.toHaveBeenCalled()
  })

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
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now, useLegalDongDirectory: false }).resolve_area!
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
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now, useLegalDongDirectory: false }).resolve_area!

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
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now, useLegalDongDirectory: false }).resolve_area!

    const result = await connector({ query: '수원 영통구', country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>

    expect(result.data).toEqual([{
      administrative_code: '4111710500',
      display_name: '경기도 수원시 영통구 영통동',
      boundary_version: 'JUSO_LIVE_UNVERSIONED',
      match_kind: 'CONTAINS',
    }])
    expect(fetcher).toHaveBeenCalledOnce()
  })

  it('ranks dong matches before myeon and ri matches and displays the matched ri', async () => {
    const fetcher = vi.fn(async (input: unknown) => {
      const keyword = new URL(String(input)).searchParams.get('keyword')
      const juso = keyword === '성수동'
        ? [{ admCd: '1120011500', siNm: '서울특별시', sggNm: '성동구', emdNm: '성수동1가' }]
        : keyword === '성수면'
          ? [{ admCd: '4575031021', siNm: '전북특별자치도', sggNm: '임실군', emdNm: '성수면' }]
          : keyword === '성수리'
            ? [{ admCd: '4719025636', siNm: '경상북도', sggNm: '구미시', emdNm: '산동읍', liNm: '성수리' }]
            : []
      return new Response(JSON.stringify({ results: { common: { errorCode: '0' }, juso } }), { status: 200 })
    })
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now, useLegalDongDirectory: false }).resolve_area!

    const result = await connector({ query: '성수', country_code: 'KR', limit: 10 }, scope) as { data: Array<{ display_name: string }> }

    expect(result.data.map((candidate) => candidate.display_name)).toEqual([
      '서울특별시 성동구 성수동1가',
      '전북특별자치도 임실군 성수면',
      '경상북도 구미시 산동읍 성수리',
    ])
  })

  it('retries one transient official API failure before returning candidates', async () => {
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        results: { common: { errorCode: '0' }, juso: [
          { admCd: '1144012300', siNm: '서울특별시', sggNm: '마포구', emdNm: '망원동' },
        ] },
      }), { status: 200 }))
    const connector = createConnectorRegistry({ jusoApiKey: 'configured', fetch: fetcher as typeof fetch, now: () => now, useLegalDongDirectory: false }).resolve_area!

    const result = await connector({ query: '망원동', country_code: 'KR', limit: 5 }, scope) as Record<string, unknown>

    expect(result).toMatchObject({ status: 'OK' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('returns explicit partial evidence instead of a domain error when the live fallback is unavailable', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('upstream unavailable')
    })
    const connector = createConnectorRegistry({
      jusoApiKey: 'configured',
      fetch: fetcher as typeof fetch,
      now: () => now,
      useLegalDongDirectory: false,
    }).resolve_area!

    const result = await connector({ query: '검색 불가 지명', country_code: 'KR', limit: 5 }, scope)

    expect(result).toMatchObject({
      status: 'PARTIAL',
      data: [],
      missing_fields: ['administrative_area'],
      error_codes: ['SOURCE_UNAVAILABLE'],
    })
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
