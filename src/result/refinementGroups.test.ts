import { describe, expect, it } from 'vitest'
import type { DecisionInput, DocumentType } from '../apiClient'
import { buildRefinementGroups } from './refinementGroups'

function input(
  field: string,
  type: NonNullable<DecisionInput['resolution_action']>['type'],
  accepted_document_types: DocumentType[] = [],
): DecisionInput {
  return {
    field,
    label: field,
    range: { currency: 'KRW', low: 1, base: 2, high: 3, provenance_refs: [] },
    provenance: 'ASSUMPTION',
    resolution_status: 'DECLARED_ASSUMPTION',
    decision_role: 'FINANCE_INPUT',
    source: null,
    applied_to: ['INITIAL_CASH'],
    replaceable_by: [type],
    limitation_code: null,
    resolution_action: { type, target_fields: [field], accepted_document_types },
  }
}

describe('refinement action grouping', () => {
  it('groups all property-driven inputs behind one property action', () => {
    const groups = buildRefinementGroups([
      input('DEPOSIT', 'PROPERTY_TERMS'),
      input('ACQUISITION_OR_PREMIUM', 'PROPERTY_TERMS'),
      input('MONTHLY_OCCUPANCY', 'PROPERTY_TERMS'),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].title).toBe('실제 점포 조건')
    expect(groups[0].inputs.map((value) => value.field)).toEqual([
      'DEPOSIT',
      'ACQUISITION_OR_PREMIUM',
      'MONTHLY_OCCUPANCY',
    ])
    expect(groups[0].representative.field).toBe('MONTHLY_OCCUPANCY')
    expect(groups[0].acceptedDocumentTypes).toEqual(['PROPERTY_LISTING', 'COMMERCIAL_LEASE'])
  })

  it('keeps interior, equipment and franchise documents as separate user actions', () => {
    const groups = buildRefinementGroups([
      input('CONSTRUCTION', 'DOCUMENT_INTAKE', ['INTERIOR_QUOTE']),
      input('EQUIPMENT', 'DOCUMENT_INTAKE', ['EQUIPMENT_QUOTE']),
      input('FRANCHISE_INITIAL_FEES', 'DOCUMENT_INTAKE', ['FRANCHISE_DISCLOSURE', 'FRANCHISE_AGREEMENT']),
    ])

    expect(groups.map((group) => group.kind)).toEqual(['INTERIOR_QUOTE', 'EQUIPMENT_QUOTE', 'FRANCHISE_COSTS'])
    expect(groups.map((group) => group.actionLabel)).toEqual([
      '인테리어 견적 반영하기',
      '장비 견적 반영하기',
      '가맹비 문서 반영하기',
    ])
  })
})