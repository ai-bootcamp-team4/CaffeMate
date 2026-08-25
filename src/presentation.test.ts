import { describe, expect, it } from 'vitest'
import { displayFounderValue, formatRange } from './presentation'

describe('founder condition presentation', () => {
  it('shows borrowing values as user choices rather than internal review states', () => {
    expect(displayFounderValue('borrowing_intent', 'NO')).toBe('대출 안 함')
    expect(displayFounderValue('borrowing_intent', 'YES')).toBe('대출 고려')
    expect(displayFounderValue('borrowing_intent', 'UNDECIDED')).toBe('미정')
  })

  it('shows cafe and operation enums as concrete choices', () => {
    expect(displayFounderValue('cafe_type_preference', 'FRANCHISE_ONLY')).toBe('프랜차이즈만')
    expect(displayFounderValue('operation_mode', 'EMPLOYEE_LED')).toBe('직원 중심 운영')
  })
})

describe('financial range presentation', () => {
  it('shows only the minimum and maximum when an amount has a range', () => {
    expect(formatRange({ currency: 'KRW', low: 10_000_000, base: 15_000_000, high: 20_000_000, provenance_refs: [] }))
      .toBe('10,000,000원 ~ 20,000,000원')
  })

  it('shows one amount when the minimum and maximum are identical', () => {
    expect(formatRange({ currency: 'KRW', low: 10_000_000, base: 10_000_000, high: 10_000_000, provenance_refs: [] }))
      .toBe('10,000,000원')
  })
})
