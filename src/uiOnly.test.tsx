import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import App from './App'
import { createUiOnlyDependencies } from './uiOnly'

afterEach(cleanup)

async function completeUiOnlyOnboarding() {
  fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
  await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })
  fireEvent.change(screen.getByLabelText('희망 지역'), { target: { value: '수원 원천동' } })
  fireEvent.click(await screen.findByRole('option', { name: /경기도 수원시 영통구 원천동/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.change(screen.getByLabelText('현재 자기자금'), { target: { value: '8000' } })
  fireEvent.click(screen.getByRole('radio', { name: /아직 미정/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /둘 다 비교/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /직접 전업 운영/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('button', { name: '분석 시작' }))
  await screen.findByRole('heading', { name: '소형 포장 중심 개인카페' })
}

describe('UI-only development mode', () => {
  it('opens the onboarding UI without Firebase or a Control API', async () => {
    const { authGateway, apiFactory } = createUiOnlyDependencies()
    render(<App authGateway={authGateway} apiFactory={apiFactory} />)

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))

    expect(await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })).toBeTruthy()
  })

  it('demonstrates benchmark grounding, external checks, and an actual-property decision flip', async () => {
    const { authGateway, apiFactory } = createUiOnlyDependencies()
    render(<App authGateway={authGateway} apiFactory={apiFactory} />)
    await completeUiOnlyOnboarding()

    expect(screen.getByRole('button', { name: /예시 프랜차이즈 후보/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /소형 좌석 균형형 개인카페/ })).toBeTruthy()
    expect(screen.getByText('한국부동산원 상업용부동산 임대동향조사 · UI 예시')).toBeTruthy()
    expect(screen.getAllByText('장비비').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: /예시 프랜차이즈 후보/ }))
    expect(screen.getByText('이 주소의 출점 가능 여부')).toBeTruthy()
    expect(screen.getByText(/CaffeMate가 확정할 수 없습니다/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /소형 포장 중심 개인카페/ }))
    fireEvent.click(screen.getByRole('button', { name: '실제 조건으로 검증하기' }))
    await screen.findByRole('heading', { name: '실제 조건으로 검증하기' })
    fireEvent.click(screen.getByRole('button', { name: '이 조건으로 다시 판단' }))

    expect(await screen.findByRole('heading', { name: '무엇이 바뀌어서 판단이 달라졌나요?' })).toBeTruthy()
    expect(screen.getByText(/지역 참고값.*실제 입력으로 확인/)).toBeTruthy()
    expect(screen.getByText(/자금 조건: 통과.*막힘/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '결과 비교로 돌아가기' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: '왜 이 안은 지금 진행하기 어려운가요?' })).toBeTruthy())
    expect(screen.getByText('최소 부족액')).toBeTruthy()
    expect(screen.getByText('5,000,000원')).toBeTruthy()
  })
})
