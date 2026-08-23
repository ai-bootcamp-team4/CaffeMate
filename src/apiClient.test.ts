import { describe, expect, it, vi } from 'vitest'
import { createControlApiClient, ControlApiError } from './apiClient'
import type { AuthSession } from './auth'

const session: AuthSession = {
  uid: 'user-1',
  displayName: null,
  getIdToken: vi.fn(async () => 'firebase-token'),
  signOut: vi.fn(async () => undefined),
}

describe('ControlApiClient', () => {
  it('sends a Firebase bearer token and idempotency key on writes', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ project_id: 'project-1' }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    const client = createControlApiClient(session, { baseUrl: 'https://api.example.test/', fetchImpl, idempotencyKey: () => 'request-1' })

    await client.createProject()

    expect(fetchImpl).toHaveBeenCalledWith('https://api.example.test/v1/projects', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ Authorization: 'Bearer firebase-token', 'Idempotency-Key': 'request-1' }),
    }))
  })

  it('maps onboarding fields to the strict FounderState contract', async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(init?.body as string, { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const client = createControlApiClient(session, { baseUrl: 'https://api.example.test', fetchImpl, idempotencyKey: () => 'request-1' })

    const response = await client.confirmOnboarding('project-1', {
      targetAreaInput: ' 수원 원천동 ', ownFundsKrw: '80000000', borrowingIntent: 'UNDECIDED', cafeTypePreference: 'OPEN_TO_BOTH', operationMode: 'DIRECT_FULL_TIME', desiredOpeningPeriod: '', priorCafeExperience: '',
    }, 'signed-area-selection') as unknown as { founder: Record<string, unknown>; area_selection_token: string }

    expect(response.founder).toEqual(expect.objectContaining({ target_area_input: '수원 원천동', own_funds_krw: 80_000_000, desired_opening_period: null, preferences: [], avoidances: [] }))
    expect(response.area_selection_token).toBe('signed-area-selection')
  })

  it('preserves backend error codes for user-visible failure handling', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ code: 'WORKFLOW_PRECONDITION_FAILED' }), { status: 409, headers: { 'Content-Type': 'application/json' } }))
    const client = createControlApiClient(session, { baseUrl: 'https://api.example.test', fetchImpl })

    await expect(client.startFirstProposal('project-1')).rejects.toEqual(expect.objectContaining<Partial<ControlApiError>>({ status: 409, code: 'WORKFLOW_PRECONDITION_FAILED' }))
  })

  it('loads the official preparation guide for the selected candidate', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ selection_id: 'selection-1', procedures: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const client = createControlApiClient(session, { baseUrl: 'https://api.example.test', fetchImpl })

    await client.getPreparationGuide('project-1', 'selection-1')

    expect(fetchImpl).toHaveBeenCalledWith('https://api.example.test/v1/projects/project-1/candidate-selections/selection-1/preparation-guide', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer firebase-token' }),
    }))
  })

  it('saves user-confirmed property terms for deterministic recalculation', async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(init?.body as string, { status: 201, headers: { 'Content-Type': 'application/json' } }))
    const client = createControlApiClient(session, { baseUrl: 'https://api.example.test', fetchImpl, idempotencyKey: () => 'property-request-1' })

    await client.applyPropertyTerms('project-1', 'selection-1', 2, {
      address: '데모 점포 · 실매물 아님', area_sqm: 33, floor: null,
      deposit_krw: 30_000_000, monthly_rent_krw: 2_200_000,
      management_fee_krw: 200_000, key_money_krw: 10_000_000,
    })

    expect(fetchImpl).toHaveBeenCalledWith('https://api.example.test/v1/projects/project-1/candidate-selections/selection-1/property-terms', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ 'Idempotency-Key': 'property-request-1' }),
      body: expect.stringContaining('"expected_state_version":2'),
    }))
  })

  it('uploads a document with signed headers and completes the revision', async () => {
    const responses = [
      new Response(JSON.stringify({
        document_id: 'document-1', document_revision_id: 'revision-1', revision_number: 1,
        object_path: 'projects/project-1/documents/document-1/revisions/revision-1/source.pdf',
        upload_url: 'https://storage.example.test/signed', method: 'PUT',
        required_headers: { 'Content-Type': 'application/pdf', 'x-goog-meta-caffemate-sha256': 'a'.repeat(64) },
        expires_at: '2026-08-23T00:10:00Z', status: 'UPLOAD_PENDING',
      }), { status: 201, headers: { 'Content-Type': 'application/json' } }),
      new Response(null, { status: 200 }),
      new Response(JSON.stringify({ status: 'SCAN_PENDING' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    ]
    const fetchImpl = vi.fn(async () => responses.shift()!)
    const client = createControlApiClient(session, { baseUrl: 'https://api.example.test', fetchImpl, idempotencyKey: () => 'document-request-1' })
    const file = new File(['%PDF-1.7'], 'lease.pdf', { type: 'application/pdf' })

    const upload = await client.beginDocumentUpload('project-1', file, 'COMMERCIAL_LEASE', 'a'.repeat(64))
    await client.uploadDocument(upload, file)
    await client.completeDocumentUpload('project-1', upload.document_revision_id)

    expect(fetchImpl).toHaveBeenNthCalledWith(2, 'https://storage.example.test/signed', expect.objectContaining({
      method: 'PUT', body: file,
      headers: expect.objectContaining({ 'x-goog-meta-caffemate-sha256': 'a'.repeat(64) }),
    }))
    expect(fetchImpl).toHaveBeenNthCalledWith(3, 'https://api.example.test/v1/projects/project-1/documents/uploads:complete', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ document_revision_id: 'revision-1' }),
    }))
  })
})
