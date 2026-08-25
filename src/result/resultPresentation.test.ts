import { describe, expect, it } from 'vitest'
import { publicStatus } from './resultPresentation'

describe('public result status', () => {
  it('describes a non-final result without conditional-review jargon', () => {
    expect(publicStatus('CONDITIONAL_REVIEW')).toBe('추가 확인 후 판단')
  })
})
