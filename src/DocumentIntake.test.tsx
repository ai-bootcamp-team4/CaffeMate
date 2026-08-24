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
  it('runs a selected property document through upload and extraction', async () => {
    const fileArrayBuffer = Object.getOwnPropertyDescriptor(File.prototype, 'arrayBuffer')
    Object.defineProperty(File.prototype, 'arrayBuffer', {
      configurable: true,
      value: async () => new TextEncoder().encode('demo property document').buffer,
    })
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

      const file = new File(['property document'], 'property-listing.pdf', {
        type: 'application/pdf',
      })
      fireEvent.change(screen.getByLabelText('파일 선택'), { target: { files: [file] } })
      fireEvent.click(screen.getByRole('button', { name: '업로드하고 값 찾기' }))

      await waitFor(() => expect(client.beginDocumentUpload).toHaveBeenCalled())
      const [, uploadedFile, uploadedType] = vi.mocked(client.beginDocumentUpload).mock.calls[0]
      expect(uploadedFile.name).toBe('property-listing.pdf')
      expect(uploadedType).toBe('PROPERTY_LISTING')
      expect(client.uploadDocument).toHaveBeenCalled()
      expect(await screen.findByText('자동으로 채운 값')).toBeTruthy()
      expect(screen.getByDisplayValue('2200000')).toBeTruthy()
    } finally {
      if (fileArrayBuffer) Object.defineProperty(File.prototype, 'arrayBuffer', fileArrayBuffer)
      else Reflect.deleteProperty(File.prototype, 'arrayBuffer')
    }
  })

  it('keeps the user-selected document type when a file is chosen', () => {
    render(
      <DocumentIntake
        client={{} as ControlApiClient}
        projectId="project-1"
        enabled
        onApplied={async () => undefined}
      />,
    )

    fireEvent.change(screen.getByLabelText('자료 종류'), {
      target: { value: 'FRANCHISE_DISCLOSURE' },
    })
    const file = new File(['disclosure'], 'brand-disclosure.pdf', {
      type: 'application/pdf',
    })
    fireEvent.change(screen.getByLabelText('파일 선택'), {
      target: { files: [file] },
    })

    expect((screen.getByLabelText('자료 종류') as HTMLSelectElement).value).toBe(
      'FRANCHISE_DISCLOSURE',
    )
    expect(screen.getByText('자료 종류와 파일이 맞는지 확인한 뒤 값을 찾아드릴게요.')).toBeTruthy()
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
