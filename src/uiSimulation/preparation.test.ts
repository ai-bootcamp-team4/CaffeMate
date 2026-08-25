import { describe, expect, it } from 'vitest'
import type { Project, ResultCandidate } from '../apiClient'
import { buildSeongsuPreparationGuide } from './preparation'

const project = {
  project_id: 'project-1',
  user_id: 'user-1',
  title: '성수 카페 검토',
  status: 'ACTIVE',
  created_at: '2026-08-25T00:00:00Z',
  updated_at: '2026-08-25T00:00:00Z',
  state: {
    area: {
      area_id: 'legal-dong:1120011500',
      display_name: '서울특별시 성동구 성수동2가',
    },
  },
} as unknown as Project

const candidate = {
  candidate_id: 'candidate-1',
  case_type: 'INDEPENDENT',
} as ResultCandidate

describe('Seongsu preparation guide fixture', () => {
  it('covers all six backend preparation procedure categories with multiple concrete steps', () => {
    const guide = buildSeongsuPreparationGuide(project, 'selection-1', candidate)
    expect(guide.status).toBe('COMPLETE')
    expect(guide.procedures.map((procedure) => procedure.procedure_type)).toEqual([
      'FACILITY_REQUIREMENTS',
      'HYGIENE_EDUCATION',
      'FOOD_SERVICE_REPORT',
      'BUSINESS_REGISTRATION',
      'SIGNAGE',
      'FIRE_SAFETY',
    ])
    expect(guide.procedures.every((procedure) => procedure.steps.length >= 2)).toBe(true)
    expect(guide.procedures.every((procedure) => (procedure.source_trace?.length ?? 0) > 0)).toBe(true)
  })
})