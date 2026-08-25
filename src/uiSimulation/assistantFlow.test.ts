import { describe, expect, it } from 'vitest'
import { waitForWorkflow, type ControlApiClient, type ResultView } from '../apiClient'
import type { OnboardingValues } from '../onboardingState'
import { createUiOnlyDependencies } from '../uiOnly'

async function readyClient(overrides: Partial<OnboardingValues> = {}) {
  const { authGateway, apiFactory } = createUiOnlyDependencies({ workflowTimeScale: 0.001 })
  const session = await authGateway.signIn()
  const client = apiFactory(session)
  const project = await client.createProject()
  const search = await client.searchAreas(project.project_id, '성수')
  const seongsu2 = search.candidates.find((candidate) => candidate.display_name.endsWith('성수동2가'))
  if (!seongsu2) throw new Error('missing Seongsu search result')
  const values: OnboardingValues = {
    targetAreaInput: '성수',
    ownFundsKrw: '150000000',
    borrowingIntent: 'NO',
    cafeTypePreference: 'OPEN_TO_BOTH',
    operationMode: 'DIRECT_FULL_TIME',
    desiredOpeningPeriod: '',
    priorCafeExperience: '',
    ...overrides,
  }
  await client.confirmOnboarding(project.project_id, values, seongsu2.selection_token)
  const workflow = await client.startFirstProposal(project.project_id)
  await waitForWorkflow(client, project.project_id, workflow)
  return { client, projectId: project.project_id }
}

async function applyCondition(client: ControlApiClient, projectId: string, input: string) {
  const before = await client.getResult(projectId)
  const answer = await client.explainResult(projectId, before, input, before.primary_candidate_id ?? undefined)
  expect(answer.suggested_action).toBe('OPEN_CONDITION_CHANGE')
  const preview = await client.createFeedbackPreview(projectId, input)
  const stillBefore = await client.getResult(projectId)
  expect(stillBefore.result_bundle_id).toBe(before.result_bundle_id)
  expect(stillBefore.current_head.state_version).toBe(before.current_head.state_version)

  const resolution = await client.confirmFeedback(projectId, preview)
  if (resolution.workflow) await waitForWorkflow(client, projectId, resolution.workflow)
  const after = await client.getResult(projectId)
  expect(after.current_head.state_version).toBe(before.current_head.state_version + 1)
  expect(after.result_bundle_id).not.toBe(before.result_bundle_id)
  return { before, after, preview }
}

function candidate(result: ResultView, name: string) {
  return result.candidates.find((item) => item.display_name === name)
}

describe('UI-only assistant happy paths', () => {
  it('answers all six explanation intents without changing the result', async () => {
    const { client, projectId } = await readyClient()
    const result = await client.getResult(projectId)
    const cases = [
      ['왜 이 안을 먼저 보나요?', 'WHY_RECOMMENDED'],
      ['다른 후보랑 뭐가 달라?', 'COMPARE'],
      ['돈이 어떻게 계산됐어?', 'FINANCE'],
      ['월 점유비 출처가 뭐야?', 'SOURCE'],
      ['아직 확인 안 된 게 뭐야?', 'MISSING_INFO'],
      ['월세가 더 비싸지면 어떻게 돼?', 'COUNTERFACTUAL'],
    ] as const

    for (const [question, expectedIntent] of cases) {
      const answer = await client.explainResult(projectId, result, question, result.primary_candidate_id ?? undefined)
      expect(answer.intent).toBe(expectedIntent)
      expect(answer.suggested_action).toBe('NONE')
      expect(answer.state_changed).toBe(false)
    }
    expect((await client.getResult(projectId)).result_bundle_id).toBe(result.result_bundle_id)
  })

  it('applies the 100M budget scenario and rebuilds the candidate set', async () => {
    const { client, projectId } = await readyClient()
    const { before, after, preview } = await applyCondition(client, projectId, '예산을 1억으로 바꿔줘')
    expect(preview.before_founder.own_funds_krw).toBe(150_000_000)
    expect(preview.after_founder?.own_funds_krw).toBe(100_000_000)
    expect(candidate(before, '생활권 단골 균형형 개인카페')?.review_status).toBe('CONDITIONAL_REVIEW')
    expect(candidate(after, '생활권 단골 균형형 개인카페')?.review_status).toBe('EXCLUDED')
    expect(candidate(after, '이디야커피')?.decision_trace?.gates[0].metrics.own_funds_krw).toBe(100_000_000)
  })

  it('turns a funding failure into conditional review when borrowing is enabled', async () => {
    const { client, projectId } = await readyClient({ ownFundsKrw: '100000000' })
    const before = await client.getResult(projectId)
    expect(candidate(before, '이디야커피')?.review_status).toBe('EXCLUDED')

    const { after, preview } = await applyCondition(client, projectId, '대출도 고려할게')
    expect(preview.after_founder?.borrowing_intent).toBe('YES')
    expect(candidate(after, '이디야커피')?.review_status).toBe('CONDITIONAL_REVIEW')
  })

  it('filters to independent candidates', async () => {
    const { client, projectId } = await readyClient()
    const { after } = await applyCondition(client, projectId, '프랜차이즈는 빼줘')
    expect(after.candidates.length).toBeGreaterThan(0)
    expect(after.candidates.every((item) => item.case_type === 'INDEPENDENT')).toBe(true)
  })

  it('filters to the franchise candidate', async () => {
    const { client, projectId } = await readyClient()
    const { after } = await applyCondition(client, projectId, '프랜차이즈만 보고 싶어')
    expect(after.candidates).toHaveLength(1)
    expect(after.candidates[0].case_type).toBe('FRANCHISE')
  })

  it('removes the direct-operation-only seed when switching to employee-led operation', async () => {
    const { client, projectId } = await readyClient()
    const { after, preview } = await applyCondition(client, projectId, '직접 운영은 어려워')
    expect(preview.after_founder?.operation_mode).toBe('EMPLOYEE_LED')
    expect(after.candidates.some((item) => item.independent_model?.model_id === 'independent-small-takeout-v1')).toBe(false)
  })
})