import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import App from './App'
import { createUiOnlyDependencies } from './uiOnly'

afterEach(cleanup)

async function completeUiOnlyOnboarding() {
  fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))
  await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })
  fireEvent.change(screen.getByLabelText('희망 지역'), { target: { value: '성수' } })
  fireEvent.click(await screen.findByRole('option', { name: /서울특별시 성동구 성수동2가/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.change(screen.getByLabelText('현재 자기자금'), { target: { value: '15000' } })
  fireEvent.click(screen.getByRole('radio', { name: /고려하지 않음/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /둘 다 비교/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('radio', { name: /직접 전업 운영/ }))
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  fireEvent.click(screen.getByRole('button', { name: '분석 시작' }))
  await screen.findByRole('heading', { name: '가치·속도 회전형 개인카페' })
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

    expect(screen.getByRole('button', { name: /이디야커피/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /생활권 단골 균형형 개인카페/ })).toBeTruthy()
    expect(screen.getAllByText('한국부동산원 상업용부동산 임대동향조사').length).toBeGreaterThan(0)
    expect(screen.getAllByText('장비비').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '실제값으로 바꿔서 다시 계산하기' })).toBeTruthy()
    const propertyGroup = screen.getByRole('article', { name: '실제 점포 조건' })
    expect(within(propertyGroup).getByText('보증금')).toBeTruthy()
    expect(within(propertyGroup).getByText('권리금·양수비')).toBeTruthy()
    expect(within(propertyGroup).getByText('월 점유비')).toBeTruthy()
    expect(within(propertyGroup).getAllByRole('button', { name: '실제 매물로 바꾸기' })).toHaveLength(1)
    expect(screen.getByRole('button', { name: '장비 견적 반영하기' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '인테리어 견적 반영하기' })).toBeTruthy()

    const independentExternal = screen.getByRole('region', { name: 'CaffeMate 밖에서 확인해야 해요' })
    expect(within(independentExternal).getByText('점포 시설기준 최종 확인')).toBeTruthy()
    expect(within(independentExternal).getByText('점포별 소방안전 적용 확인')).toBeTruthy()
    expect(within(independentExternal).getByText('임대인 공사·업종 동의 확인')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '장비 견적 반영하기' }))
    expect(await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })).toBeTruthy()
    expect(screen.getByRole('option', { name: '장비 견적서' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: '인테리어 견적서' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))

    fireEvent.click(screen.getByRole('button', { name: '인테리어 견적 반영하기' }))
    expect(await screen.findByRole('option', { name: '인테리어 견적서' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: '장비 견적서' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))

    fireEvent.click(screen.getByRole('button', { name: /이디야커피/ }))
    expect(screen.getByText('이 주소의 출점 가능 여부')).toBeTruthy()
    expect(screen.getByText(/CaffeMate가 확정할 수 없습니다/)).toBeTruthy()
    expect(screen.getByText('영업지역 보호 범위 확인')).toBeTruthy()
    expect(screen.getByText('본사 점포·설계 승인')).toBeTruthy()
    expect(screen.getByRole('button', { name: '가맹비 문서 반영하기' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '로열티 문서 반영하기' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '가맹비 문서 반영하기' }))
    expect(await screen.findByRole('option', { name: '프랜차이즈 정보공개서' })).toBeTruthy()
    expect(screen.getByRole('option', { name: '가맹계약서' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))

    fireEvent.click(screen.getByRole('button', { name: '창업 준비 절차 보기' }))
    expect(await screen.findByText('신규 영업자 위생교육 이수')).toBeTruthy()
    expect(screen.getByText('휴게음식점 영업신고 준비')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '시설 기준 확인' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '사업자등록' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '간판 신고 확인' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '소방 안전 확인' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /가치·속도 회전형 개인카페/ }))
    fireEvent.click(screen.getByRole('button', { name: '실제 매물로 바꾸기' }))
    await screen.findByRole('heading', { name: '실제 숫자로 정밀화하기' })
    fireEvent.click(screen.getByRole('button', { name: '파일로 불러오기' }))
    const listing = new File(['ui-only'], 'listing.pdf', { type: 'application/pdf' })
    Object.defineProperty(listing, 'arrayBuffer', { value: async () => new TextEncoder().encode('ui-only').buffer })
    fireEvent.change(screen.getByLabelText('파일 선택'), { target: { files: [listing] } })
    fireEvent.click(screen.getByRole('button', { name: '업로드하고 값 찾기' }))
    expect(await screen.findByDisplayValue('6500000')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '이 값으로 점포 입력 채우기' }))
    expect((await screen.findByLabelText('월세(만원)') as HTMLInputElement).value).toBe('650')
    fireEvent.click(screen.getByRole('button', { name: '이 조건으로 다시 판단' }))

    expect(await screen.findByRole('heading', { name: '무엇이 바뀌어서 판단이 달라졌나요?' })).toBeTruthy()
    expect(screen.getByText(/지역 참고값.*실제 입력으로 확인/)).toBeTruthy()
    expect(screen.getByText(/자금 조건: 조건부.*막힘/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '결과로 돌아가기' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: '왜 이 안은 지금 진행하기 어려운가요?' })).toBeTruthy())
    expect(screen.getByText('최소 부족액')).toBeTruthy()
    expect(screen.getAllByText('39,500,000원').length).toBeGreaterThan(0)
  }, 15_000)
})
