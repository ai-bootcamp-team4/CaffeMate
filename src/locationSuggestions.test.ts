import { describe, expect, it } from 'vitest'
import { findLocationSuggestions } from './locationSuggestions'

describe('findLocationSuggestions', () => {
  it('finds a district from its familiar short name', () => {
    expect(findLocationSuggestions('성수')[0]).toMatchObject({
      district: '성수동',
      value: '서울 성동구 성수동',
    })
  })

  it('finds a district by municipality and ignores blank queries', () => {
    expect(findLocationSuggestions('영통').map(({ district }) => district)).toContain('원천동')
    expect(findLocationSuggestions('   ')).toEqual([])
  })
})
