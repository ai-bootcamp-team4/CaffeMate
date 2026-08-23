import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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

describe('DocumentIntake', () => {
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
