import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import App from './App'

afterEach(cleanup)

async function completeOnboarding() {
  fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
  fireEvent.change(screen.getByLabelText('희망 지역'), { target: { value: '수원 원천동' } })
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.change(screen.getByLabelText('현재 자기자금'), { target: { value: '8000' } })
  fireEvent.click(screen.getByRole('radio', { name: /아직 미정/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /둘 다 비교/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /직접 전업 운영/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('button', { name: '분석 시작' }))
  await waitFor(() => expect(screen.getByRole('heading', { name: '결과 피드백' })).toBeTruthy(), { timeout: 1400 })
}

describe('CaffeMate onboarding and result feedback', () => {
  it('shows onboarding before feedback and validates the first required bundle', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: '카페 창업, 감이 아닌 데이터로 시작하세요.' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
    expect(screen.getByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '결과 피드백' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '다음' }))
    expect(screen.getByText('이 단계의 필수 항목을 선택해 주세요.')).toBeTruthy()
  })

  it('suggests and selects a related district while entering a location', () => {
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))

    fireEvent.change(screen.getByRole('combobox', { name: '희망 지역' }), { target: { value: '성수' } })
    fireEvent.click(screen.getByRole('option', { name: /성수동.*서울 성동구/ }))

    expect((screen.getByRole('combobox', { name: '희망 지역' }) as HTMLInputElement).value).toBe('서울 성동구 성수동')
    expect(screen.queryByRole('listbox', { name: '연관 지역' })).toBeNull()
  })

  it('converts convenient money units into the same underlying amount', () => {
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
    fireEvent.change(screen.getByLabelText('희망 지역'), { target: { value: '서울 성수동' } })
    fireEvent.click(screen.getByRole('button', { name: '다음' }))

    const input = screen.getByLabelText('현재 자기자금') as HTMLInputElement
    const unit = screen.getByLabelText('금액 단위') as HTMLSelectElement
    fireEvent.change(input, { target: { value: '8000' } })
    expect(input.value).toBe('8,000')
    expect(screen.getByText('입력 금액: 8,000만원')).toBeTruthy()

    fireEvent.change(unit, { target: { value: '백만원' } })
    expect(input.value).toBe('80')
    fireEvent.change(unit, { target: { value: '천만원' } })
    expect(input.value).toBe('8')
    fireEvent.change(unit, { target: { value: '억원' } })
    expect(input.value).toBe('0.8')
    expect(input.placeholder).toBe('예: 0.8')
    expect(screen.getByText('입력 금액: 8,000만원')).toBeTruthy()
  })

  it('keeps the feedback composer visible beside the completed result', async () => {
    render(<App />)
    await completeOnboarding()

    expect(screen.getByRole('heading', { name: '결과 피드백' })).toBeTruthy()
    expect(screen.getByLabelText('자연어 피드백')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '먼저 결론부터 검토합니다' })).toBeTruthy()
  })

  it('switches between generated candidates and updates the visible plan', async () => {
    render(<App />)
    await completeOnboarding()

    expect(screen.getByRole('tab', { name: /추천 창업안.*브랜드 A 소형점/ }).getAttribute('aria-selected')).toBe('true')
    fireEvent.click(screen.getByRole('tab', { name: /다른 후보 1.*브랜드 B 테이크아웃점/ }))

    expect(screen.getByRole('heading', { name: '브랜드 B 테이크아웃점' })).toBeTruthy()
    expect(screen.getByText('약 7–10평')).toBeTruthy()
    expect(screen.getAllByText('7,600만–1억 900만 원').length).toBeGreaterThan(0)
    expect(screen.getByRole('tab', { name: /다른 후보 1.*브랜드 B 테이크아웃점/ }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: /추천 창업안.*브랜드 A 소형점/ }).getAttribute('aria-selected')).toBe('false')
    expect(screen.getAllByRole('tab').filter((tab) => tab.getAttribute('aria-controls') === 'candidate-report' && tab.getAttribute('aria-selected') === 'true')).toHaveLength(1)

    fireEvent.click(screen.getByRole('tab', { name: /다른 후보 2.*개인카페 컴팩트형/ }))
    expect(screen.getByRole('heading', { name: '개인카페 컴팩트형' })).toBeTruthy()
    expect(screen.getByText('약 9–12평')).toBeTruthy()
  })

  it('shows a proposed diff without applying it and supports cancel', async () => {
    render(<App />)
    await completeOnboarding()

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
    await completeOnboarding()

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
