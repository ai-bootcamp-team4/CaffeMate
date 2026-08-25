import { describe, expect, it } from 'vitest'
import { createUiOnlyDependencies } from '../uiOnly'
import type { OnboardingValues } from '../onboardingState'

const marker = /ui[-_ ]?only|simulat|fixture|demo|미리보기|가상 매물|UI 전용|UI 예시/i

function stringValues(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(stringValues)
  if (value && typeof value === 'object') return Object.values(value).flatMap(stringValues)
  return []
}

function expectProductionShaped(value: unknown) {
  expect(stringValues(value).filter((item) => marker.test(item))).toEqual([])
}

const values: OnboardingValues = {
  targetAreaInput: '성수',
  ownFundsKrw: '150000000',
  borrowingIntent: 'NO',
  cafeTypePreference: 'OPEN_TO_BOTH',
  operationMode: 'DIRECT_FULL_TIME',
  desiredOpeningPeriod: '',
  priorCafeExperience: '',
}

describe('development dependency public payload boundary', () => {
  it('never tells the frontend that the Seongsu payload came from local data', async () => {
    const { authGateway, apiFactory } = createUiOnlyDependencies({ workflowTimeScale: 0.001 })
    const session = await authGateway.signIn()
    expectProductionShaped(session)
    const client = apiFactory(session)

    const created = await client.createProject()
    expectProductionShaped(created)

    const search = await client.searchAreas(created.project_id, '성수')
    expectProductionShaped(search)
    const seongsu2 = search.candidates.find((candidate) => candidate.display_name.endsWith('성수동2가'))
    if (!seongsu2) throw new Error('missing Seongsu search result')

    const confirmed = await client.confirmOnboarding(created.project_id, values, seongsu2.selection_token)
    expectProductionShaped(confirmed)
    const initial = await client.getResult(created.project_id)
    expectProductionShaped(initial)

    const workflow = await client.startFirstProposal(created.project_id)
    expectProductionShaped(workflow)
    expectProductionShaped(await client.getWorkflow(created.project_id, workflow.workflow_run_id))

    const explanation = await client.explainResult(created.project_id, initial, '왜 이 후보를 먼저 보나요?')
    expectProductionShaped(explanation)

    const candidateId = initial.primary_candidate_id ?? initial.candidates[0].candidate_id
    const selection = await client.selectCandidate(created.project_id, initial, candidateId)
    expectProductionShaped(selection)
    expectProductionShaped(await client.getPreparationGuide(created.project_id, selection.selection_id))

    const property = await client.applyPropertyTerms(created.project_id, selection.selection_id, initial.current_head.state_version, {
      address: '서울특별시 성동구 연무장길 57',
      area_sqm: 33.1,
      floor: '1층',
      deposit_krw: 80_000_000,
      monthly_rent_krw: 6_500_000,
      management_fee_krw: 700_000,
      key_money_krw: 50_000_000,
    })
    expect(property.is_demo_fixture).toBe(false)
    expectProductionShaped(property)
    const propertyResult = await client.getResult(created.project_id)
    expectProductionShaped(propertyResult)
    const propertyCandidate = propertyResult.candidates.find((candidate) => candidate.candidate_id === candidateId)
    expect(propertyCandidate?.decision_inputs?.find((input) => input.field === 'MONTHLY_OCCUPANCY')?.range?.base).toBe(7_200_000)
    expect(propertyCandidate?.decision_trace?.gates[0].status).toBe('FAIL')

    const file = new File(['quote'], '성수-장비견적서.pdf', { type: 'application/pdf' })
    const upload = await client.beginDocumentUpload(created.project_id, file, 'EQUIPMENT_QUOTE', 'a'.repeat(64))
    expectProductionShaped(upload)
    await client.uploadDocument(upload, file)
    const revision = await client.completeDocumentUpload(created.project_id, upload.document_revision_id)
    expectProductionShaped(revision)
    const form = await client.getDocumentExtractionForm(created.project_id, revision.document_revision_id)
    expectProductionShaped(form)
    const application = await client.applyDocumentExtractionForm(created.project_id, form)
    expectProductionShaped(application)
    const documentResult = await client.getResult(created.project_id)
    expectProductionShaped(documentResult)
    const documentCandidate = documentResult.candidates.find((candidate) => candidate.candidate_id === candidateId)
    const equipment = documentCandidate?.decision_inputs?.find((input) => input.field === 'EQUIPMENT')
    expect(equipment?.range?.base).toBe(21_500_000)
    expect(equipment?.source?.filename).toBe('성수-장비견적서.pdf')
  })
})
