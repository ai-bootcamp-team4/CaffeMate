import { type FormEvent, useState } from 'react'
import { DocumentIntake } from '../DocumentIntake'
import { PreparationProcedures } from '../PreparationProcedures'
import type { CandidateSelection, ControlApiClient, PreparationGuide, PropertyTermsApplication, PropertyTermsInput, ResultCandidate, ResultView } from '../apiClient'
import { Badge } from '../ui/Badge'
import { decisionInputLabel, refinableInputs } from '../result/resultPresentation'
import { DecisionDelta } from './DecisionDelta'
import './Verification.css'

export type PropertyRecalculation = {
  mode: 'LIVE'
  application: PropertyTermsApplication
  candidate: ResultCandidate
  result: ResultView
}

const demoPropertyTerms = {
  address: '서울 마포구 공덕동 데모 점포 · 실매물 아님',
  area_sqm: '33',
  floor: '1층',
  deposit_manwon: '3000',
  monthly_rent_manwon: '220',
  management_fee_manwon: '20',
  key_money_manwon: '1000',
}

export function VerificationFlow({
  client,
  projectId,
  candidate,
  selection,
  guide,
  busy,
  error,
  onLoadProcedures,
  onBack,
  onApply,
  onDocumentApplied,
}: {
  client: ControlApiClient
  projectId: string
  candidate: ResultCandidate
  selection: CandidateSelection
  guide: PreparationGuide | null
  busy: boolean
  error: string
  onLoadProcedures: () => void
  onBack: () => void
  onApply: (terms: PropertyTermsInput) => Promise<PropertyRecalculation>
  onDocumentApplied: () => Promise<void>
}) {
  const [values, setValues] = useState(demoPropertyTerms)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('첫 분석의 참고값을 실제 점포 조건으로 교체할 수 있어요.')
  const [outcome, setOutcome] = useState<PropertyRecalculation | null>(null)
  const [officialOpen, setOfficialOpen] = useState(false)
  const inputs = refinableInputs(candidate)
  const documentInputs = inputs.filter((input) => input.resolution_action?.type === 'DOCUMENT_INTAKE')
  const externalRequirements = candidate.verification_requirements ?? []

  const setValue = (key: keyof typeof demoPropertyTerms, value: string) => setValues((current) => ({ ...current, [key]: value }))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setStatus('입력한 점포 조건으로 비용과 판정을 다시 계산하고 있어요.')
    try {
      const next = await onApply({
        address: values.address.trim(),
        area_sqm: Number(values.area_sqm),
        floor: values.floor.trim() || null,
        deposit_krw: Number(values.deposit_manwon) * 10_000,
        monthly_rent_krw: Number(values.monthly_rent_manwon) * 10_000,
        management_fee_krw: Number(values.management_fee_manwon) * 10_000,
        key_money_krw: values.key_money_manwon === '' ? null : Number(values.key_money_manwon) * 10_000,
      })
      setOutcome(next)
      setStatus('실제 점포 조건을 반영해 판단을 다시 계산했습니다.')
    } catch (caught) {
      setStatus(caught instanceof Error && /[가-힣]/.test(caught.message) ? caught.message : '점포 조건을 반영하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.')
    } finally {
      setSaving(false)
    }
  }

  const openProcedures = () => {
    setOfficialOpen(true)
    if (!guide && !busy) onLoadProcedures()
  }

  return (
    <div className="shell min-h-dvh">
      <header className="topbar">
        <a className="wordmark" href="#verificationTop">CaffeMate</a>
        <div className="topbar__meta"><Badge tone="success">검토 대상 선택됨</Badge></div>
      </header>
      <main className="page verification-page" id="verificationTop">
        <header className="verification-hero">
          <div>
            <p className="result-kicker">선택한 창업안 · {candidate.display_name}</p>
            <h1>실제 조건으로 검증하기</h1>
            <p>참고값을 실제 점포·견적·계약 숫자로 하나씩 교체하고, 그때마다 CaffeMate 판단이 어떻게 달라지는지 확인합니다.</p>
          </div>
          <button className="btn btn--accent" type="button" onClick={onBack}>결과 비교로 돌아가기</button>
        </header>

        <ol className="verification-steps" aria-label="검증 순서">
          <li>1 실제 점포 조건</li><li>2 견적·계약 숫자</li><li>3 새 조건으로 다시 판단</li><li>4 외부 확인</li><li>5 창업 준비 절차</li>
        </ol>

        <section className="verification-section" aria-labelledby="propertyStepTitle">
          <header className="verification-section__head">
            <p className="result-kicker">1 · 실제 점포 조건</p>
            <h2 id="propertyStepTitle">지역 참고값을 실제 임대 조건으로 교체해요</h2>
            <p>첫 분석의 임대 관련 값은 지역 참고값일 수 있습니다. 실제 보증금·월세·관리비·권리금을 입력하면 해당 항목을 섞지 않고 교체해 다시 계산합니다.</p>
          </header>
          <div className="demo-input-actions">
            <button className="btn btn--accent" type="button" onClick={() => setValues(demoPropertyTerms)}>데모 입력 예시 불러오기</button>
          </div>
          <p className="demo-input-note"><strong>데모 입력 예시</strong>는 입력 형식 확인용이며 실매물·공식 근거가 아닙니다.</p>
          <form className="property-form" onSubmit={submit}>
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
        </section>

        <section className="verification-section" aria-labelledby="evidenceStepTitle">
          <header className="verification-section__head">
            <p className="result-kicker">2 · 견적·계약 숫자</p>
            <h2 id="evidenceStepTitle">참고 가정을 실제 문서 숫자로 교체해요</h2>
            <p>문서 업로드 자체가 목적이 아니라, 아래처럼 아직 미확정인 계산 입력을 실제 값으로 바꾸는 단계입니다.</p>
          </header>
          {documentInputs.length > 0 ? (
            <>
              <div className="verification-targets">{documentInputs.map((input) => <span key={input.field}>{decisionInputLabel(input)} · 문서로 교체</span>)}</div>
              <DocumentIntake client={client} projectId={projectId} enabled={selection.document_intake_enabled} onApplied={onDocumentApplied} onViewResult={onBack} />
            </>
          ) : (
            <p>현재 결과에는 문서 숫자로 교체할 계산 입력이 없습니다.</p>
          )}
        </section>

        {outcome && <DecisionDelta delta={outcome.result.decision_delta} candidate={outcome.candidate} previousFinancialSummary={outcome.application.previous_financial_summary} />}

        <section className="verification-section" aria-labelledby="externalStepTitle">
          <header className="verification-section__head">
            <p className="result-kicker">4 · CaffeMate 밖에서 확인</p>
            <h2 id="externalStepTitle">계산으로 끝낼 수 없는 조건을 따로 확인해요</h2>
            <p>이 단계는 재계산 입력이 아니라 본사·관할기관 등 최종 확인 주체가 결정하는 항목입니다.</p>
          </header>
          {externalRequirements.length ? <ul className="action-list">{externalRequirements.map((requirement) => <li key={requirement.requirement_code}><div><strong>{requirement.label}</strong><p>{requirement.reason}</p></div><span>{requirement.authority ?? requirement.resolver}</span></li>)}</ul> : <p>현재 구조화된 외부 확인 요구사항이 없습니다.</p>}
        </section>

        <section className="verification-section verification-boundary" aria-labelledby="boundaryTitle">
          <header className="verification-section__head">
            <p className="result-kicker">CaffeMate 검토는 여기까지</p>
            <h2 id="boundaryTitle">계산과 근거 정리는 돕지만 최종 행동은 대신하지 않아요</h2>
          </header>
          <p>계약 체결, 송금·결제, 대출 실행, 법적 확정 판단, 고액 지출과 최종 창업 결정은 사용자가 직접 합니다.</p>
        </section>

        <section className="verification-section" aria-labelledby="procedureStepTitle">
          <header className="verification-section__head">
            <p className="result-kicker">5 · 창업 준비 절차</p>
            <h2 id="procedureStepTitle">경제·계약 검토 뒤에 공식 절차를 확인해요</h2>
            <p>초기 판단과 섞지 않고, 실제 안을 더 검토하기로 한 뒤 관할 기준의 준비 절차를 불러옵니다.</p>
          </header>
          {!officialOpen ? (
            <button className="btn btn--primary" type="button" onClick={openProcedures}>창업 준비 절차 보기</button>
          ) : (
            <PreparationProcedures guide={guide} busy={busy} error={error} onRetry={onLoadProcedures} />
          )}
        </section>
      </main>
      <footer className="footer"><strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span></footer>
    </div>
  )
}
