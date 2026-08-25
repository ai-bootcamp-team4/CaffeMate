import type { DecisionInput, DocumentType } from '../apiClient'

export type RefinementGroupKind =
  | 'PROPERTY_TERMS'
  | 'INTERIOR_QUOTE'
  | 'EQUIPMENT_QUOTE'
  | 'FRANCHISE_COSTS'
  | 'LOAN_TERMS'
  | 'DOCUMENT'
  | 'USER_INPUT'

export interface RefinementGroup {
  key: string
  kind: RefinementGroupKind
  title: string
  description: string
  actionLabel: string
  inputs: DecisionInput[]
  representative: DecisionInput
  acceptedDocumentTypes: DocumentType[]
}

function actionable(input: DecisionInput) {
  const action = input.resolution_action
  if (!action || action.type === 'NONE' || action.type === 'EXTERNAL_CONFIRMATION') return false
  if (action.type !== 'PROPERTY_TERMS' && ['USER_CONFIRMED_FACT', 'RESOLVED_FACT'].includes(input.resolution_status)) return false
  return true
}

function documentKind(types: DocumentType[]): RefinementGroupKind {
  if (types.includes('INTERIOR_QUOTE')) return 'INTERIOR_QUOTE'
  if (types.includes('EQUIPMENT_QUOTE')) return 'EQUIPMENT_QUOTE'
  if (types.includes('FRANCHISE_DISCLOSURE') || types.includes('FRANCHISE_AGREEMENT')) return 'FRANCHISE_COSTS'
  if (types.includes('LOAN_TERMS')) return 'LOAN_TERMS'
  return 'DOCUMENT'
}

function groupKind(input: DecisionInput): RefinementGroupKind | null {
  const action = input.resolution_action
  if (!action) return null
  if (action.type === 'PROPERTY_TERMS') return 'PROPERTY_TERMS'
  if (action.type === 'USER_INPUT') return 'USER_INPUT'
  if (action.type === 'DOCUMENT_INTAKE') return documentKind(action.accepted_document_types ?? [])
  return null
}

function meta(kind: RefinementGroupKind): Pick<RefinementGroup, 'title' | 'description' | 'actionLabel'> {
  if (kind === 'PROPERTY_TERMS') {
    return {
      title: '실제 점포 조건',
      description: '주소·면적·층·보증금·월세·관리비·권리금을 한 번에 입력해 점포 관련 비용을 같이 교체합니다.',
      actionLabel: '실제 매물로 바꾸기',
    }
  }
  if (kind === 'INTERIOR_QUOTE') {
    return {
      title: '인테리어 견적',
      description: '공사 범위와 견적 총액을 확인해 등록 가정 대신 실제 견적 숫자로 다시 계산합니다.',
      actionLabel: '인테리어 견적 반영하기',
    }
  }
  if (kind === 'EQUIPMENT_QUOTE') {
    return {
      title: '장비 견적',
      description: '커피머신·그라인더·제빙기 등 장비 견적을 반영해 장비비를 실제 조건으로 교체합니다.',
      actionLabel: '장비 견적 반영하기',
    }
  }
  if (kind === 'FRANCHISE_COSTS') {
    return {
      title: '프랜차이즈 초기비용',
      description: '정보공개서나 가맹계약서의 가맹비·교육비·가맹보증금·기타 초기비용을 한 묶음으로 확인합니다.',
      actionLabel: '가맹비 문서 반영하기',
    }
  }
  if (kind === 'LOAN_TERMS') {
    return {
      title: '대출 조건',
      description: '금리·기간·상환 조건을 확인해 자금 계획에 필요한 실제 조건을 반영합니다.',
      actionLabel: '대출 조건 반영하기',
    }
  }
  if (kind === 'USER_INPUT') {
    return {
      title: '직접 확인할 값',
      description: '현재 판단에 필요한 실제 값을 직접 입력해 다시 계산합니다.',
      actionLabel: '직접 값 입력하기',
    }
  }
  return {
    title: '관련 문서 값',
    description: '관련 문서를 반영해 현재 가정이나 미확인 값을 실제 조건으로 교체합니다.',
    actionLabel: '문서 값 반영하기',
  }
}

function groupKey(input: DecisionInput, kind: RefinementGroupKind) {
  if (kind === 'USER_INPUT' || kind === 'DOCUMENT') return `${kind}:${input.field}`
  return kind
}

function chooseRepresentative(kind: RefinementGroupKind, inputs: DecisionInput[]) {
  if (kind === 'PROPERTY_TERMS') {
    return inputs.find((input) => input.field === 'MONTHLY_OCCUPANCY')
      ?? inputs.find((input) => input.field === 'DEPOSIT')
      ?? inputs[0]
  }
  return inputs[0]
}

export function buildRefinementGroups(inputs: DecisionInput[]): RefinementGroup[] {
  const grouped = new Map<string, { kind: RefinementGroupKind; inputs: DecisionInput[] }>()
  for (const input of inputs) {
    if (!actionable(input)) continue
    const kind = groupKind(input)
    if (!kind) continue
    const key = groupKey(input, kind)
    const existing = grouped.get(key)
    if (existing) existing.inputs.push(input)
    else grouped.set(key, { kind, inputs: [input] })
  }

  const order: RefinementGroupKind[] = [
    'PROPERTY_TERMS',
    'INTERIOR_QUOTE',
    'EQUIPMENT_QUOTE',
    'FRANCHISE_COSTS',
    'LOAN_TERMS',
    'DOCUMENT',
    'USER_INPUT',
  ]

  return [...grouped.entries()]
    .map(([key, value]) => {
      const acceptedDocumentTypes = value.kind === 'PROPERTY_TERMS'
        ? ['PROPERTY_LISTING', 'COMMERCIAL_LEASE'] as DocumentType[]
        : [...new Set(value.inputs.flatMap((input) => input.resolution_action?.accepted_document_types ?? []))]
      return {
        key,
        kind: value.kind,
        ...meta(value.kind),
        inputs: value.inputs,
        representative: chooseRepresentative(value.kind, value.inputs),
        acceptedDocumentTypes,
      }
    })
    .sort((left, right) => order.indexOf(left.kind) - order.indexOf(right.kind))
}