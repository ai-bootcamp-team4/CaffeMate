import { type FormEvent, useState } from 'react'
import { DocumentIntake } from '../DocumentIntake'
import type {
  CandidateSelection,
  ControlApiClient,
  DecisionInput,
  DocumentExtractionForm,
  PropertyTermsApplication,
  PropertyTermsInput,
  ResultCandidate,
  ResultView,
} from '../apiClient'
import { internalLabel } from '../presentation'
import { buildRefinementGroups } from '../result/refinementGroups'
import { decisionInputLabel, decisionInputValue, financeInputs, provenanceLabel } from '../result/resultPresentation'
import { DecisionDelta } from './DecisionDelta'
import './Refinement.css'

export type PropertyRecalculation = {
  mode: 'LIVE'
  application: PropertyTermsApplication
  candidate: ResultCandidate
  result: ResultView
}

export type DocumentRecalculation = {
  candidate: ResultCandidate
  result: ResultView
  previousFinancialSummary: ResultCandidate['financial_summary']
}

const initialPropertyTerms = {
  address: '',
  area_sqm: '',
  floor: '',
  deposit_manwon: '',
  monthly_rent_manwon: '',
  management_fee_manwon: '',
  key_money_manwon: '',
}

export function NumericRefinementFlow({
  client,
  projectId,
  candidate,
  selection,
  target,
  onBack,
  onApplyProperty,
  onDocumentApplied,
}: {
  client: ControlApiClient
  projectId: string
  candidate: ResultCandidate
  selection: CandidateSelection
  target: DecisionInput
  onBack: () => void
  onApplyProperty: (terms: PropertyTermsInput) => Promise<PropertyRecalculation>
  onDocumentApplied: () => Promise<DocumentRecalculation>
}) {
  const [values, setValues] = useState(initialPropertyTerms)
  const [propertyInputMode, setPropertyInputMode] = useState<'MANUAL' | 'DOCUMENT'>('MANUAL')
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('현재 참고값을 실제 숫자로 교체할 수 있어요.')
  const [propertyOutcome, setPropertyOutcome] = useState<PropertyRecalculation | null>(null)
  const [documentOutcome, setDocumentOutcome] = useState<DocumentRecalculation | null>(null)
  const action = target.resolution_action
  const isProperty = action?.type === 'PROPERTY_TERMS'
  const isDocument = action?.type === 'DOCUMENT_INTAKE'
  const refinementGroup = buildRefinementGroups(financeInputs(candidate))
    .find((group) => group.inputs.some((input) => input.field === target.field))
  const groupedInputs = refinementGroup?.inputs ?? [target]
  const affectedCalculations = [...new Set(groupedInputs.flatMap((input) => input.applied_to))]
  const setValue = (key: keyof typeof initialPropertyTerms, value: string) => setValues((current) => ({ ...current, [key]: value }))

  const submitProperty = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setStatus('입력한 점포 조건으로 비용과 판정을 다시 계산하고 있어요.')
    try {
      const next = await onApplyProperty({
        address: values.address.trim(),
        area_sqm: Number(values.area_sqm),
        floor: values.floor.trim() || null,
        deposit_krw: Number(values.deposit_manwon) * 10_000,
        monthly_rent_krw: Number(values.monthly_rent_manwon) * 10_000,
        management_fee_krw: Number(values.management_fee_manwon) * 10_000,
        key_money_krw: values.key_money_manwon === '' ? null : Number(values.key_money_manwon) * 10_000,
      })
      setPropertyOutcome(next)
      setStatus('실제 점포 조건을 반영해 판단을 다시 계산했습니다.')
    } catch (caught) {
      setStatus(caught instanceof Error && /[가-힣]/.test(caught.message) ? caught.message : '점포 조건을 반영하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.')
    } finally {
      setSaving(false)
    }
  }

  const documentApplied = async () => {
    const next = await onDocumentApplied()
    setDocumentOutcome(next)
  }

  const propertyValuesFromDocument = async (form: DocumentExtractionForm) => {
    const byClaim = new Map(form.fields.map((field) => [field.claim_type, field.current_value]))
    const moneyInManwon = (claimType: string, fallback: string) => {
      const value = byClaim.get(claimType)
      return typeof value === 'number' ? String(value / 10_000) : fallback
    }
    const textValue = (claimType: string, fallback: string) => {
      const value = byClaim.get(claimType)
      return typeof value === 'string' && value.trim() ? value.trim() : fallback
    }
    const areaValue = byClaim.get('AREA')
    setValues((current) => ({
      ...current,
      address: textValue('ADDRESS', current.address),
      area_sqm: typeof areaValue === 'number' ? String(areaValue) : current.area_sqm,
      floor: textValue('FLOOR', current.floor),
      deposit_manwon: moneyInManwon('LEASE_DEPOSIT', current.deposit_manwon),
      monthly_rent_manwon: moneyInManwon('MONTHLY_RENT', current.monthly_rent_manwon),
      management_fee_manwon: moneyInManwon('MANAGEMENT_FEE', current.management_fee_manwon),
      key_money_manwon: moneyInManwon('KEY_MONEY', current.key_money_manwon),
    }))
    setPropertyInputMode('MANUAL')
    setStatus('파일에서 찾은 값을 입력란에 채웠어요. 빠진 값과 주소·면적을 확인한 뒤 한 번에 반영해 주세요.')
  }

  const outcome = propertyOutcome
    ? { candidate: propertyOutcome.candidate, result: propertyOutcome.result, previousFinancialSummary: propertyOutcome.application.previous_financial_summary }
    : documentOutcome

  return (
    <main className="page refinement-page" id="refinementTop">
      <header className="refinement-hero">
        <div>
          <p className="result-kicker">선택한 창업안 · {candidate.display_name}</p>
          <h1>실제 숫자로 정밀화하기</h1>
          <p>첫 분석에서 사용한 참고값이나 가정을 실제 점포·견적·계약 숫자로 교체하고 판단 변화를 확인합니다.</p>
        </div>
        <button className="btn btn--accent" type="button" onClick={onBack}>결과로 돌아가기</button>
      </header>

      <section className="refinement-target" aria-labelledby="refinementTargetTitle">
        <div>
          <p className="result-kicker">이번에 함께 바꿀 값</p>
          <h2 id="refinementTargetTitle">{refinementGroup?.title ?? decisionInputLabel(target)}</h2>
          {refinementGroup && <p>{refinementGroup.description}</p>}
        </div>
        <dl>
          <div>
            <dt>현재 값</dt>
            <dd className="refinement-target__values">
              {groupedInputs.map((input) => (
                <span key={input.field}><b>{decisionInputLabel(input)}</b>{decisionInputValue(input)} · {provenanceLabel(input)}</span>
              ))}
            </dd>
          </div>
          <div><dt>같이 바뀌는 항목</dt><dd>{groupedInputs.map((input) => decisionInputLabel(input)).join(' · ')}</dd></div>
          <div><dt>영향받는 계산</dt><dd>{affectedCalculations.length ? affectedCalculations.map((item) => internalLabel(item, '계산 항목')).join(' · ') : '재계산 입력'}</dd></div>
        </dl>
      </section>

      {isProperty && (
        <section className="refinement-section" aria-labelledby="propertyStepTitle">
          <header className="refinement-section__head">
            <p className="result-kicker">실제 점포 입력</p>
            <h2 id="propertyStepTitle">지역 참고값을 실제 임대 조건으로 교체해요</h2>
            <p>보증금·월세·관리비·권리금과 점포 정보를 한 번에 반영해 다시 계산합니다.</p>
          </header>
          <div className="property-input-mode" role="group" aria-label="실제 점포 입력 방법">
            <button className="btn btn--accent" aria-pressed={propertyInputMode === 'MANUAL'} type="button" onClick={() => setPropertyInputMode('MANUAL')}>직접 입력</button>
            <button className="btn btn--accent" aria-pressed={propertyInputMode === 'DOCUMENT'} type="button" onClick={() => setPropertyInputMode('DOCUMENT')}>파일로 불러오기</button>
          </div>

          {propertyInputMode === 'MANUAL' ? (
            <>
              <form className="property-form" onSubmit={submitProperty}>
                <label className="field"><span>점포 주소</span><input required value={values.address} onChange={(event) => setValue('address', event.target.value)} /></label>
                <label className="field"><span>면적(㎡)</span><input required min="1" step="0.1" type="number" value={values.area_sqm} onChange={(event) => setValue('area_sqm', event.target.value)} /></label>
                <label className="field"><span>층</span><input value={values.floor} onChange={(event) => setValue('floor', event.target.value)} /></label>
                <label className="field"><span>보증금(만원)</span><input required min="0" type="number" value={values.deposit_manwon} onChange={(event) => setValue('deposit_manwon', event.target.value)} /></label>
                <label className="field"><span>월세(만원)</span><input required min="0" type="number" value={values.monthly_rent_manwon} onChange={(event) => setValue('monthly_rent_manwon', event.target.value)} /></label>
                <label className="field"><span>관리비(만원)</span><input required min="0" type="number" value={values.management_fee_manwon} onChange={(event) => setValue('management_fee_manwon', event.target.value)} /></label>
                <label className="field"><span>권리금(만원)</span><input min="0" type="number" value={values.key_money_manwon} onChange={(event) => setValue('key_money_manwon', event.target.value)} /></label>
                <div className="property-form__action">
                  <button className="btn btn--primary" disabled={saving || !selection.property_intake_enabled} type="submit">{saving ? '재계산 중' : '이 조건으로 다시 판단'}</button>
                  <p aria-live="polite">{status}</p>
                </div>
              </form>
            </>
          ) : (
            <DocumentIntake
              client={client}
              projectId={projectId}
              enabled={selection.document_intake_enabled}
              acceptedDocumentTypes={['PROPERTY_LISTING', 'COMMERCIAL_LEASE']}
              targetLabel="실제 점포 임대 조건"
              usePreparedValuesLabel="이 값으로 점포 입력 채우기"
              onUsePreparedValues={propertyValuesFromDocument}
              onApplied={async () => {}}
            />
          )}
        </section>
      )}

      {isDocument && (
        <section className="refinement-section" aria-labelledby="documentRefinementTitle">
          <header className="refinement-section__head">
            <p className="result-kicker">견적·계약 숫자</p>
            <h2 id="documentRefinementTitle">{decisionInputLabel(target)}을 실제 문서 숫자로 교체해요</h2>
            <p>업로드한 문서에서 값을 자동으로 채운 뒤 수정하고 한 번에 반영합니다.</p>
          </header>
          <DocumentIntake
            client={client}
            projectId={projectId}
            enabled={selection.document_intake_enabled}
            acceptedDocumentTypes={action.accepted_document_types}
            targetLabel={decisionInputLabel(target)}
            onApplied={documentApplied}
          />
        </section>
      )}

      {!isProperty && !isDocument && (
        <section className="refinement-section">
          <p className="contract-gap">이 입력 유형은 아직 별도 입력 UI가 연결되지 않았습니다.</p>
        </section>
      )}

      {outcome && (
        <DecisionDelta
          delta={outcome.result.decision_delta}
          candidate={outcome.candidate}
          previousFinancialSummary={outcome.previousFinancialSummary}
        />
      )}
    </main>
  )
}
