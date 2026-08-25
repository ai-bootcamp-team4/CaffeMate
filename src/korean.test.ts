import { describe, expect, it } from 'vitest'
import { withParticle } from './korean'

describe('withParticle', () => {
  it.each([
    ['카페', '을/를', '카페를'],
    ['회전형', '을/를', '회전형을'],
    ['이디야커피', '과/와', '이디야커피와'],
    ['창업안', '은/는', '창업안은'],
    ['후보', '이/가', '후보가'],
    ['선택', '이/가', '선택이'],
    ['서울', '으로/로', '서울로'],
    ['성수', '으로/로', '성수로'],
    ['상권', '으로/로', '상권으로'],
  ] as const)('%s + %s becomes %s', (word, pair, expected) => {
    expect(withParticle(word, pair)).toBe(expected)
  })
})
