import { describe, expect, it } from 'vitest'
import { searchSimulationAreas, simulationAreaByToken } from './scenarios'

describe('area search fixtures', () => {
  it('returns a realistic first page of geographic matches for 성수', () => {
    const results = searchSimulationAreas('성수')
    expect(results).toHaveLength(8)
    expect(results.slice(0, 2).map((area) => area.display_name)).toEqual([
      '서울특별시 성동구 성수동1가',
      '서울특별시 성동구 성수동2가',
    ])
    expect(results.map((area) => area.display_name)).toEqual(expect.arrayContaining([
      '전북특별자치도 임실군 성수면',
      '전북특별자치도 임실군 성수면 도인리',
      '전북특별자치도 임실군 성수면 봉강리',
      '전북특별자치도 임실군 성수면 삼봉리',
    ]))
    expect(JSON.stringify(results)).not.toMatch(/ui[-_ ]?only|simulat|fixture|demo/i)
  })

  it('narrows a city-qualified query without inventing market-area choices', () => {
    expect(searchSimulationAreas('서울 성수').map((area) => area.display_name)).toEqual([
      '서울특별시 성동구 성수동1가',
      '서울특별시 성동구 성수동2가',
    ])
  })

  it('supports deep analysis only for the Seoul Seongsu selections', () => {
    const results = searchSimulationAreas('성수')
    expect(simulationAreaByToken(results[0].selection_token)?.analysis_key).toBe('SEOUL_SEONGSU_1GA')
    expect(simulationAreaByToken(results[1].selection_token)?.analysis_key).toBe('SEOUL_SEONGSU_2GA')
    expect(simulationAreaByToken(results[2].selection_token)).toBeNull()
  })

  it('returns no fallback when the address search does not match', () => {
    expect(searchSimulationAreas('없는동네테스트')).toEqual([])
  })
})
