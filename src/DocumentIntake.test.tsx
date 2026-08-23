import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DocumentIntake } from './DocumentIntake'
import type { ControlApiClient } from './apiClient'

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
})
