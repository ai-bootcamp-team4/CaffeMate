import { describe, expect, it } from 'vitest'
import { canContinue, formatKoreanKrw, formatKrw, initialOnboardingValues } from './onboardingState'

describe('onboarding state', () => {
  it('requires only the four confirmed onboarding bundles', () => {
    expect(canContinue(0, initialOnboardingValues)).toBe(false)
    expect(canContinue(0, { ...initialOnboardingValues, targetAreaInput: '수원 원천동' })).toBe(true)
    expect(canContinue(1, { ...initialOnboardingValues, ownFundsKrw: '0', borrowingIntent: 'UNDECIDED' })).toBe(true)
    expect(canContinue(2, { ...initialOnboardingValues, cafeTypePreference: 'OPEN_TO_BOTH' })).toBe(true)
    expect(canContinue(3, { ...initialOnboardingValues, operationMode: 'UNDECIDED' })).toBe(true)
  })

  it('formats entered funds without inventing a value', () => {
    expect(formatKrw('80000000')).toBe('80,000,000원')
    expect(formatKrw('')).toBe('입력 전')
  })

  it('formats large won amounts with Korean eok and manwon units', () => {
    expect(formatKoreanKrw('5000000')).toBe('500만원')
    expect(formatKoreanKrw('80000000')).toBe('8,000만원')
    expect(formatKoreanKrw('100000000')).toBe('1억원')
    expect(formatKoreanKrw('150000000')).toBe('1억 5,000만원')
    expect(formatKoreanKrw('800000000')).toBe('8억원')
    expect(formatKoreanKrw('1250000000')).toBe('12억 5,000만원')
  })
})
