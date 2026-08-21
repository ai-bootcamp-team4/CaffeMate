import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import App from './App'

afterEach(cleanup)

describe('CaffeMate result feedback', () => {
  it('keeps the feedback composer visible beside the completed result', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: '결과 피드백' })).toBeTruthy()
    expect(screen.getByLabelText('자연어 피드백')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '먼저 결론부터 검토합니다' })).toBeTruthy()
  })

  it('shows a proposed diff without applying it and supports cancel', () => {
    render(<App />)

    fireEvent.change(screen.getByLabelText('자연어 피드백'), {
      target: { value: '저가 브랜드는 빼고 10평 이하로 보고 싶어' },
    })
    fireEvent.click(screen.getByRole('button', { name: '제안 만들기' }))

    expect(screen.getByRole('heading', { name: '적용 전 변경 확인' })).toBeTruthy()
    expect(screen.getByText('저가 브랜드 제외')).toBeTruthy()
    expect(screen.getByText('10평 이하')).toBeTruthy()
    expect(screen.getByText(/결과 미반영/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '제안 취소' }))

    expect(screen.queryByRole('heading', { name: '적용 전 변경 확인' })).toBeNull()
    expect(screen.getByText(/결과는 바뀌지 않았습니다/)).toBeTruthy()
  })

  it('applies only the confirmed proposal', async () => {
    render(<App />)

    fireEvent.change(screen.getByLabelText('자연어 피드백'), {
      target: { value: '저가 브랜드는 빼줘' },
    })
    fireEvent.click(screen.getByRole('button', { name: '제안 만들기' }))
    fireEvent.click(screen.getByRole('button', { name: '변경 적용' }))

    expect(screen.getByRole('button', { name: '변경 적용 중' }).hasAttribute('disabled')).toBe(true)
    await waitFor(() => expect(screen.getByText('적용 조건 · 저가 브랜드 제외')).toBeTruthy(), {
      timeout: 1200,
    })
    expect(screen.getByText(/변경 적용 완료/)).toBeTruthy()
  })
})
