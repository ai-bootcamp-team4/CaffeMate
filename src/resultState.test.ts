import { describe, expect, it } from 'vitest'
import { buildFeedbackProposal, initialResultState, resultReducer } from './resultState'

describe('resultReducer', () => {
  it('keeps the current result unchanged while a proposal waits for confirmation', () => {
    const proposal = buildFeedbackProposal('저가 브랜드는 빼고 10평 이하로 보고 싶어')
    const next = resultReducer(initialResultState, { type: 'feedback.proposed', proposal })

    expect(next.feedbackPhase).toBe('review')
    expect(next.feedbackStatus).toContain('결과 미반영')
    expect(next.appliedConditions).toEqual([])
    expect(next.history).toHaveLength(1)
  })

  it('cancels a proposal without mutating result conditions', () => {
    const proposed = resultReducer(initialResultState, {
      type: 'feedback.proposed',
      proposal: buildFeedbackProposal('저가 브랜드는 빼줘'),
    })
    const cancelled = resultReducer(proposed, { type: 'feedback.proposal.cancelled' })

    expect(cancelled.feedbackPhase).toBe('editing')
    expect(cancelled.proposal).toBeNull()
    expect(cancelled.appliedConditions).toEqual([])
  })

  it('applies only a confirmed proposal and appends the conversation history', () => {
    const proposed = resultReducer(initialResultState, {
      type: 'feedback.proposed',
      proposal: buildFeedbackProposal('저가 브랜드는 빼고 10평 이하로 보고 싶어'),
    })
    const applying = resultReducer(proposed, { type: 'feedback.apply.started' })
    const applied = resultReducer(applying, { type: 'feedback.apply.completed' })

    expect(applying.appliedConditions).toEqual([])
    expect(applied.appliedConditions).toEqual(['저가 브랜드 제외', '10평 이하'])
    expect(applied.history).toHaveLength(3)
    expect(applied.feedbackStatus).toContain('확인 후 반영됨')
  })

  it('supports exclusion with a reversible undo state', () => {
    const excluded = resultReducer(initialResultState, { type: 'candidate.excluded' })
    const restored = resultReducer(excluded, { type: 'candidate.exclude.undone' })

    expect(excluded.excluded).toBe(true)
    expect(excluded.toastVisible).toBe(true)
    expect(restored.excluded).toBe(false)
    expect(restored.toastVisible).toBe(false)
  })
})
