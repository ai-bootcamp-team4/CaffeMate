import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import App from './App'
import { createUiOnlyDependencies } from './uiOnly'

afterEach(cleanup)

describe('UI-only development mode', () => {
  it('opens the onboarding UI without Firebase or a Control API', async () => {
    const { authGateway, apiFactory } = createUiOnlyDependencies()
    render(<App authGateway={authGateway} apiFactory={apiFactory} />)

    fireEvent.click(screen.getByRole('button', { name: /내 카페 창업 분석 시작하기/ }))

    expect(await screen.findByRole('heading', { name: '창업을 고민 중인 지역을 알려주세요.' })).toBeTruthy()
  })
})
