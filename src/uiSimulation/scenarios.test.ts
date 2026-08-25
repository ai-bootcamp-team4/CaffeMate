import { describe, expect, it } from 'vitest'
import { searchSimulationAreas, simulationAreaCatalogue } from './scenarios'

describe('UI simulation area scenarios', () => {
  it('keeps a broad catalogue of distinct local search scenarios', () => {
    expect(simulationAreaCatalogue.length).toBeGreaterThanOrEqual(16)
    expect(new Set(simulationAreaCatalogue.map((area) => area.profile_key)).size).toBeGreaterThanOrEqual(8)
  })

  it('returns multiple plausible matches for ambiguous neighborhood searches', () => {
    const seongsu = searchSimulationAreas('성수')
    expect(seongsu.map((area) => area.display_name)).toEqual(expect.arrayContaining([
      '서울특별시 성동구 성수동1가',
      '서울특별시 성동구 성수동2가',
    ]))

    const gangnam = searchSimulationAreas('강남')
    expect(gangnam.length).toBeGreaterThanOrEqual(2)
  })

  it('supports common aliases and city-plus-neighborhood queries', () => {
    expect(searchSimulationAreas('홍대').some((area) => area.display_name.includes('서교동'))).toBe(true)
    expect(searchSimulationAreas('부산 전포').some((area) => area.display_name.includes('전포동'))).toBe(true)
    expect(searchSimulationAreas('광교').some((area) => area.display_name.includes('이의동'))).toBe(true)
    expect(searchSimulationAreas('송도').some((area) => area.display_name.includes('송도동'))).toBe(true)
  })

  it('returns no fabricated fallback when nothing matches', () => {
    expect(searchSimulationAreas('없는동네테스트')).toEqual([])
  })
})