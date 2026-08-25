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
    const { authGateway, apiFactory } = createUiOnlyDependencies({ workflowTimeScale: 0.001 })
    render(<App authGateway={authGateway} apiFactory={apiFactory} />)

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))

    expect(await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })).toBeTruthy()
  })

  it('demonstrates benchmark grounding, external checks, and an actual-property decision flip', async () => {
    const { authGateway, apiFactory } = createUiOnlyDependencies({ workflowTimeScale: 0.001 })
    render(<App authGateway={authGateway} apiFactory={apiFactory} />)
    await completeUiOnlyOnboarding()

    expect(screen.getByRole('button', { name: /예시 프랜차이즈 후보/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /소형 좌석 균형형 개인카페/ })).toBeTruthy()
    expect(screen.getByText('지역 임차비 참고 범위 · UI 시뮬레이션')).toBeTruthy()
    expect(screen.getAllByText('장비비').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '실제 매물로 바꾸기' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '장비 견적 반영하기' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '인테리어 견적 반영하기' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '장비 견적 반영하기' }))
    expect(await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })).toBeTruthy()
    expect(screen.getByRole('option', { name: '장비 견적서' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: '인테리어 견적서' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))

    fireEvent.click(screen.getByRole('button', { name: '인테리어 견적 반영하기' }))
    expect(await screen.findByRole('option', { name: '인테리어 견적서' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: '장비 견적서' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))

    fireEvent.click(screen.getByRole('button', { name: /예시 프랜차이즈 후보/ }))
    expect(screen.getByText('이 주소의 출점 가능 여부')).toBeTruthy()
    expect(screen.getByText(/CaffeMate가 확정할 수 없습니다/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '가맹비 문서 반영하기' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '로열티 문서 반영하기' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '가맹비 문서 반영하기' }))
    expect(await screen.findByRole('option', { name: '프랜차이즈 정보공개서' })).toBeTruthy()
    expect(screen.getByRole('option', { name: '가맹계약서' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))

    fireEvent.click(screen.getByRole('button', { name: '창업 준비 절차 보기' }))
    expect(await screen.findByText('신규 영업자 위생교육 이수')).toBeTruthy()
    expect(screen.getByText('휴게음식점 영업신고 준비')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /소형 포장 중심 개인카페/ }))
    fireEvent.click(screen.getByRole('button', { name: '실제 매물로 바꾸기' }))
    await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })
    fireEvent.click(screen.getByRole('button', { name: '파일로 불러오기' }))
    const listing = new File(['ui-only'], 'listing.pdf', { type: 'application/pdf' })
    Object.defineProperty(listing, 'arrayBuffer', { value: async () => new TextEncoder().encode('ui-only').buffer })
    fireEvent.change(screen.getByLabelText('파일 선택'), { target: { files: [listing] } })
    fireEvent.click(screen.getByRole('button', { name: '업로드하고 값 찾기' }))
    expect(await screen.findByDisplayValue('2100000')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '이 값으로 점포 입력 채우기' }))
    expect((await screen.findByLabelText('월세(만원)') as HTMLInputElement).value).toBe('210')
    fireEvent.click(screen.getByRole('button', { name: '이 조건으로 다시 판단' }))

    expect(await screen.findByRole('heading', { name: '무엇이 바뀌어서 판단이 달라졌나요?' })).toBeTruthy()
    expect(screen.getByText(/지역 참고값.*실제 입력으로 확인/)).toBeTruthy()
    expect(screen.getByText(/자금 조건: 통과.*막힘/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: '왜 이 안은 지금 진행하기 어려운가요?' })).toBeTruthy())
    expect(screen.getByText('최소 부족액')).toBeTruthy()
    expect(screen.getByText('5,000,000원')).toBeTruthy()
  }, 15_000)
})
