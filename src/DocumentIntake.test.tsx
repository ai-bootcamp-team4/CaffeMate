import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DocumentIntake } from './DocumentIntake'
import type { ControlApiClient, DocumentExtractionForm } from './apiClient'
import { changedExtractionFields } from './documentExtractionValues'

const extractionForm: DocumentExtractionForm = {
  form_id: 'form-1',
  project_id: 'project-1',
  document_id: 'document-1',
  document_revision_id: 'revision-1',
  expected_state_version: 2,
  form_status: 'READY',
  apply_label: '반영하고 다시 계산',
  form_digest: `sha256:${'a'.repeat(64)}`,
  applied_state_version: null,
  fields: [
    {
      field_id: 'monthly-rent',
      claim_type: 'MONTHLY_RENT',
      label: '월세',
      raw_value_text: '220만원',
      extracted_value: 2_200_000,
      current_value: 2_200_000,
      unit: 'KRW',
      materiality: 'HIGH',
      extraction_status: 'AUTO_FILLED',
      edit_status: 'UNCHANGED',
      anchor: { page_index: 0, section_path: '임대 조건' },
      warnings: [],
    },
    {
      field_id: 'address',
      claim_type: 'ADDRESS',
      label: '주소',
      raw_value_text: '서울 마포구 공덕동',
      extracted_value: '서울 마포구 공덕동',
      current_value: '서울 마포구 공덕동',
      unit: null,
      materiality: 'MEDIUM',
      extraction_status: 'AUTO_FILLED',
      edit_status: 'UNCHANGED',
      anchor: { page_index: 0, section_path: '점포 정보' },
      warnings: [],
    },
  ],
}

afterEach(() => cleanup())

describe('DocumentIntake', () => {
  it('runs the bundled property document through the real upload and extraction path', async () => {
    const fileArrayBuffer = Object.getOwnPropertyDescriptor(File.prototype, 'arrayBuffer')
    Object.defineProperty(File.prototype, 'arrayBuffer', {
      configurable: true,
      value: async () => new TextEncoder().encode('demo property document').buffer,
    })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['demo property document'], { type: 'application/pdf' }),
    } as Response)
    const client = {
      beginDocumentUpload: vi.fn().mockResolvedValue({
        document_id: 'document-1',
        document_revision_id: 'revision-1',
        revision_number: 1,
        object_path: 'demo/property.pdf',
        upload_url: 'https://storage.example.test/upload',
        method: 'PUT',
        required_headers: { 'Content-Type': 'application/pdf' },
        expires_at: '2026-08-24T01:00:00Z',
        status: 'UPLOAD_PENDING',
      }),
      uploadDocument: vi.fn().mockResolvedValue(undefined),
      completeDocumentUpload: vi.fn().mockResolvedValue(undefined),
      getDocumentRevision: vi.fn().mockResolvedValue({ status: 'EXTRACTION_READY' }),
      getDocumentExtractionForm: vi.fn().mockResolvedValue(extractionForm),
    } as unknown as ControlApiClient

    try {
      render(
        <DocumentIntake
          client={client}
          projectId="project-1"
          enabled
          onApplied={async () => undefined}
        />,
      )

      fireEvent.click(screen.getByRole('button', { name: '데모 자료로 검증하기' }))

      await waitFor(() => expect(client.beginDocumentUpload).toHaveBeenCalled())
      const [, uploadedFile, uploadedType] = vi.mocked(client.beginDocumentUpload).mock.calls[0]
      expect(uploadedFile.name).toBe('05_demo_property_listing.pdf')
      expect(uploadedType).toBe('PROPERTY_LISTING')
      expect(client.uploadDocument).toHaveBeenCalled()
      expect(await screen.findByText('자동으로 채운 값')).toBeTruthy()
      expect(screen.getByDisplayValue('2200000')).toBeTruthy()
    } finally {
      fetchSpy.mockRestore()
      if (fileArrayBuffer) Object.defineProperty(File.prototype, 'arrayBuffer', fileArrayBuffer)
      else Reflect.deleteProperty(File.prototype, 'arrayBuffer')
    }
  })

  it('keeps the demo action retryable when the bundled file cannot be loaded', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false } as Response)
    const client = { beginDocumentUpload: vi.fn() } as unknown as ControlApiClient

    try {
      render(
        <DocumentIntake
          client={client}
          projectId="project-1"
          enabled
          onApplied={async () => undefined}
        />,
      )

      fireEvent.click(screen.getByRole('button', { name: '데모 자료로 검증하기' }))

      expect((await screen.findByRole('alert')).textContent).toBe('데모 자료를 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.')
      expect(client.beginDocumentUpload).not.toHaveBeenCalled()
      expect((screen.getByRole('button', { name: '데모 자료로 검증하기' }) as HTMLButtonElement).disabled).toBe(false)
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('selects the matching document type for a bundled demo file', () => {
    render(
      <DocumentIntake
        client={{} as ControlApiClient}
        projectId="project-1"
        enabled
        onApplied={async () => undefined}
      />,
    )

    const file = new File(['demo'], '02_demo_franchise_disclosure_summary.pdf', {
      type: 'application/pdf',
    })
    fireEvent.change(screen.getByLabelText('파일 선택'), {
      target: { files: [file] },
    })

    expect((screen.getByLabelText('자료 종류') as HTMLSelectElement).value).toBe(
      'FRANCHISE_DISCLOSURE',
    )
    expect(screen.getByText('데모 파일에 맞는 자료 종류를 자동으로 선택했어요.')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: '업로드하고 값 찾기' }) as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('preserves numeric types and sends only values the user changed', () => {
    expect(changedExtractionFields(extractionForm, {
      'monthly-rent': '2,000,000',
      address: '서울 마포구 공덕동',
    })).toEqual([{ field_id: 'monthly-rent', value: 2_000_000 }])
  })

  it('does not rewrite untouched extracted values', () => {
    expect(changedExtractionFields(extractionForm, {
      'monthly-rent': '2200000',
      address: '서울 마포구 공덕동',
    })).toEqual([])
  })
})
