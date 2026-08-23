import type { DocumentExtractionField, DocumentExtractionForm } from './apiClient'

export function valueForExtractionInput(value: string | number | boolean | null): string {
  if (value === null) return ''
  return String(value)
}

function typedValueForInput(
  field: DocumentExtractionField,
  rawValue: string,
): string | number | boolean | null {
  const value = rawValue.trim()
  if (!value) return null

  const referenceValue = field.current_value ?? field.extracted_value
  if (typeof referenceValue === 'number') {
    const parsed = Number(value.replaceAll(',', ''))
    if (!Number.isFinite(parsed)) throw new Error(`${field.label}에는 숫자를 입력해 주세요.`)
    return parsed
  }
  if (typeof referenceValue === 'boolean') {
    if (value === 'true' || value === '예') return true
    if (value === 'false' || value === '아니오') return false
    throw new Error(`${field.label}에는 예 또는 아니오를 입력해 주세요.`)
  }
  return value
}

export function changedExtractionFields(
  form: DocumentExtractionForm,
  values: Record<string, string>,
): Array<{ field_id: string; value: string | number | boolean | null }> {
  return form.fields.flatMap((field) => {
    const rawValue = values[field.field_id] ?? ''
    if (rawValue === valueForExtractionInput(field.current_value)) return []
    return [{ field_id: field.field_id, value: typedValueForInput(field, rawValue) }]
  })
}
