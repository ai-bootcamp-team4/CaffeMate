import { useState } from 'react'
import {
  type ControlApiClient,
  type DocumentExtractionForm,
  type DocumentType,
  sha256File,
  waitForWorkflow,
} from './apiClient'

const documentTypes: Array<{ value: DocumentType; label: string }> = [
  { value: 'PROPERTY_LISTING', label: '점포 매물 자료' },
  { value: 'COMMERCIAL_LEASE', label: '상가 임대차계약서' },
  { value: 'INTERIOR_QUOTE', label: '인테리어 견적서' },
  { value: 'EQUIPMENT_QUOTE', label: '장비 견적서' },
  { value: 'FRANCHISE_DISCLOSURE', label: '프랜차이즈 정보공개서' },
  { value: 'FRANCHISE_AGREEMENT', label: '가맹계약서' },
  { value: 'LOAN_TERMS', label: '대출 조건' },
  { value: 'OTHER', label: '기타 창업 자료' },
]

const processingStatuses = new Set(['SCAN_PENDING', 'READY_FOR_PARSING', 'PARSING'])

const demoDocumentTypes: Record<string, DocumentType> = {
  '01_demo_commercial_lease_terms.pdf': 'COMMERCIAL_LEASE',
  '02_demo_franchise_disclosure_summary.pdf': 'FRANCHISE_DISCLOSURE',
  '03_demo_interior_quote.pdf': 'INTERIOR_QUOTE',
  '04_demo_equipment_quote.pdf': 'EQUIPMENT_QUOTE',
  '05_demo_property_listing.pdf': 'PROPERTY_LISTING',
  '06_demo_loan_terms.pdf': 'LOAN_TERMS',
}

function documentError(caught: unknown, fallback: string): string {
  if (!(caught instanceof Error)) return fallback
  const message = caught.message
  if (/failed to fetch|network|load failed/i.test(message)) {
    return '서버 연결이 잠시 끊겼어요. 같은 파일로 다시 시도해 주세요.'
  }
  if (/409|stale|state|revision|precondition/i.test(message)) {
    return '입력 조건이 바뀌었어요. 최신 결과에서 문서를 다시 열어 주세요.'
  }
  if (!/[가-힣]/.test(message) || /\b[A-Z][A-Z0-9_]{2,}\b/.test(message)) return fallback
  return message
}

function valueForInput(value: string | number | boolean | null): string {
  if (value === null) return ''
  return String(value)
}

export function DocumentIntake({ client, projectId, enabled, onApplied }: {
  client: ControlApiClient
  projectId: string
  enabled: boolean
  onApplied: () => Promise<void>
}) {
  const [file, setFile] = useState<File | null>(null)
  const [documentType, setDocumentType] = useState<DocumentType>('PROPERTY_LISTING')
  const [form, setForm] = useState<DocumentExtractionForm | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('PDF, JPG, PNG, DOCX · 최대 50MB')
  const [error, setError] = useState('')

  const waitForExtraction = async (revisionId: string) => {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const revision = await client.getDocumentRevision(projectId, revisionId)
      if (revision.status === 'EXTRACTION_READY') return client.getDocumentExtractionForm(projectId, revisionId)
      if (revision.status === 'QUARANTINED' || revision.status === 'EXTRACTION_FAILED' || revision.status === 'DELETED') {
        throw new Error(revision.failure_codes[0] ?? '문서 처리에 실패했습니다.')
      }
      if (!processingStatuses.has(revision.status)) throw new Error('문서 처리 단계를 이어가지 못했어요.')
      setStatus(revision.status === 'SCAN_PENDING' ? '파일을 안전하게 검사하고 있어요.' : '문서에서 중요한 값을 찾고 있어요.')
      await new Promise((resolve) => window.setTimeout(resolve, 1500))
    }
    throw new Error('문서 확인이 오래 걸리고 있어요. 잠시 뒤 다시 업로드해 주세요.')
  }

  const upload = async () => {
    if (!file) return
    setBusy(true)
    setError('')
    setForm(null)
    try {
      setStatus('파일을 전송하고 있어요.')
      const uploadTicket = await client.beginDocumentUpload(projectId, file, documentType, await sha256File(file))
      await client.uploadDocument(uploadTicket, file)
      await client.completeDocumentUpload(projectId, uploadTicket.document_revision_id)
      const nextForm = await waitForExtraction(uploadTicket.document_revision_id)
      setForm(nextForm)
      setValues(Object.fromEntries(nextForm.fields.map((field) => [field.field_id, valueForInput(field.current_value)])))
      setStatus('자동 입력이 끝났어요. 원문과 비교해 필요한 값만 고쳐 주세요.')
    } catch (caught) {
      setError(documentError(caught, '문서를 처리하지 못했어요. 같은 파일로 다시 시도해 주세요.'))
      setStatus('다시 시도하거나 다른 파일을 선택해 주세요.')
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    if (!form) return
    setBusy(true)
    setError('')
    try {
      const updated = await client.updateDocumentExtractionForm(projectId, form, form.fields.map((field) => ({
        field_id: field.field_id,
        value: values[field.field_id]?.trim() || null,
      })))
      const application = await client.applyDocumentExtractionForm(projectId, updated)
      setStatus('확인한 값을 반영해 비용과 위험을 다시 계산하고 있어요.')
      const workflow = await client.getWorkflow(projectId, application.recompute_workflow_run_id)
      const progress = await waitForWorkflow(client, projectId, workflow)
      if (progress.status !== 'SUCCEEDED' && progress.status !== 'PARTIAL') throw new Error('재계산을 완료하지 못했습니다.')
      await onApplied()
      setStatus('문서 값을 반영하고 창업안을 다시 계산했어요.')
    } catch (caught) {
      setError(documentError(caught, '문서 값을 반영하지 못했어요. 입력값을 확인한 뒤 다시 시도해 주세요.'))
    } finally {
      setBusy(false)
    }
  }

  return <article className="surface document-intake" aria-labelledby="documentIntakeTitle">
    <div className="surface__head"><div><h2 id="documentIntakeTitle">문서로 조건 채우기</h2><p>견적서나 계약서를 올리면 중요한 값만 한 번에 확인하고 계산에 반영해요.</p></div></div>
    {!form && <div className="document-intake__upload">
      <label className="field"><span>자료 종류</span><select value={documentType} disabled={busy} onChange={(event) => setDocumentType(event.target.value as DocumentType)}>{documentTypes.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label>
      <label className="field"><span>파일 선택</span><input type="file" accept=".pdf,.jpg,.jpeg,.png,.docx,application/pdf,image/jpeg,image/png,application/vnd.openxmlformats-officedocument.wordprocessingml.document" disabled={busy} onChange={(event) => {
        const nextFile = event.target.files?.[0] ?? null
        setFile(nextFile)
        const inferredType = nextFile ? demoDocumentTypes[nextFile.name] : undefined
        if (inferredType) {
          setDocumentType(inferredType)
          setStatus('데모 파일에 맞는 자료 종류를 자동으로 선택했어요.')
        }
      }} /></label>
      <button className="btn btn--primary" type="button" disabled={!enabled || !file || busy} onClick={() => void upload()}>{busy ? '문서 확인 중' : '업로드하고 값 찾기'}</button>
    </div>}
    {form && <div className="document-extraction-form">
      <div className="document-extraction-form__intro"><strong>자동으로 채운 값</strong><p>빈 값과 검토 표시가 있는 값만 특히 확인해 주세요. 일일이 승인할 필요는 없어요.</p></div>
      {form.fields.map((field) => <label className="field document-extraction-field" key={field.field_id}>
        <span>{field.label}{field.unit ? ` (${field.unit})` : ''}</span>
        <input aria-label={`${field.label}${field.unit ? ` (${field.unit})` : ''}`} value={values[field.field_id] ?? ''} onChange={(event) => setValues((current) => ({ ...current, [field.field_id]: event.target.value }))} />
        <small>{field.anchor ? `${field.anchor.page_index + 1}쪽${field.anchor.section_path ? ` · ${field.anchor.section_path}` : ''}` : '원문 위치 확인 필요'}{field.extraction_status !== 'AUTO_FILLED' ? ' · 직접 확인이 필요한 값' : ''}</small>
      </label>)}
      <div className="document-intake__actions"><button className="btn btn--accent" type="button" disabled={busy} onClick={() => { setForm(null); setFile(null) }}>다른 문서 선택</button><button className="btn btn--primary" type="button" disabled={busy || !form.form_digest} onClick={() => void apply()}>{busy ? '다시 계산 중' : form.apply_label}</button></div>
    </div>}
    <p className="document-intake__status" aria-live="polite">{status}</p>
    {error && <p className="document-intake__error" role="alert">{error}</p>}
  </article>
}
