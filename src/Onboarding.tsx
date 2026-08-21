import { type FormEvent, type KeyboardEvent, useMemo, useState } from 'react'
import { findLocationSuggestions, type LocationSuggestion } from './locationSuggestions'
import {
  canContinue,
  formatKrw,
  formatKoreanKrw,
  initialOnboardingValues,
  type OnboardingValues,
} from './onboardingState'

const steps = [
  { short: '희망 지역', question: ['창업을 고민 중인 지역을', '알려주세요.'], hint: '입력한 지역을 기준으로 상권과 경쟁 환경을 분석합니다.' },
  { short: '자금', question: ['사용할 수 있는 자금은', '얼마인가요?'], hint: '자기자금과 대출 고려 여부를 나눠야 현실적인 비용 범위를 볼 수 있습니다.' },
  { short: '유형', question: ['어떤 카페를', '비교할까요?'], hint: '개인카페와 실제 가맹 가능한 프랜차이즈를 같은 기준으로 비교합니다.' },
  { short: '운영', question: ['가게 운영에 얼마나', '참여할까요?'], hint: '운영 방식은 인건비와 창업자 부담을 판단하는 핵심 조건입니다.' },
  { short: '확인', question: ['이 조건으로', '분석할까요?'], hint: '확인한 입력만 저장하고 분석에 사용합니다.' },
]

const cafeTypeLabels = {
  OPEN_TO_BOTH: '둘 다 비교',
  INDEPENDENT_ONLY: '개인카페만',
  FRANCHISE_ONLY: '프랜차이즈만',
}

const operationLabels = {
  DIRECT_FULL_TIME: '직접 전업 운영',
  DIRECT_PART_TIME: '시간제 참여',
  EMPLOYEE_LED: '직원 중심 운영',
  UNDECIDED: '아직 미정',
}

type MoneyUnit = '만원' | '백만원' | '천만원' | '억원'

const moneyUnits: Array<{ label: MoneyUnit; multiplier: number; placeholder: string }> = [
  { label: '만원', multiplier: 10_000, placeholder: '예: 8,000' },
  { label: '백만원', multiplier: 1_000_000, placeholder: '예: 80' },
  { label: '천만원', multiplier: 10_000_000, placeholder: '예: 8' },
  { label: '억원', multiplier: 100_000_000, placeholder: '예: 0.8' },
]

function formatMoneyInput(value: string) {
  if (!value) return ''
  const [integer = '', decimal] = value.split('.')
  const formattedInteger = integer ? Number(integer).toLocaleString('ko-KR') : '0'
  return decimal !== undefined ? `${formattedInteger}.${decimal}` : formattedInteger
}

function formatFundsSummary(value: string) {
  return `입력 금액: ${formatKoreanKrw(value)}`
}

function ChoiceGroup({
  legend,
  name,
  value,
  options,
  onChange,
}: {
  legend: string
  name: string
  value: string
  options: Array<{ value: string; label: string; description: string }>
  onChange: (value: string) => void
}) {
  return (
    <fieldset className="choice-group">
      <legend>{legend}</legend>
      <div className="choice-grid">
        {options.map((option) => (
          <label className="choice" data-selected={value === option.value} key={option.value}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            <span className="choice__mark" aria-hidden="true" />
            <span><strong>{option.label}</strong><small>{option.description}</small></span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

export default function Onboarding({ onComplete }: { onComplete: (values: OnboardingValues) => void }) {
  const [step, setStep] = useState(0)
  const [values, setValues] = useState(initialOnboardingValues)
  const [message, setMessage] = useState('필수 항목만 입력해도 분석을 시작할 수 있습니다.')
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [isLocationListOpen, setIsLocationListOpen] = useState(false)
  const [activeLocationIndex, setActiveLocationIndex] = useState(0)
  const [fundUnit, setFundUnit] = useState<MoneyUnit>('만원')
  const [fundInput, setFundInput] = useState('')
  const progress = useMemo(() => ((step + 1) / steps.length) * 100, [step])
  const suggestedLocations = useMemo(
    () => findLocationSuggestions(values.targetAreaInput),
    [values.targetAreaInput],
  )

  const update = <Key extends keyof OnboardingValues>(key: Key, value: OnboardingValues[Key]) => {
    setValues((current) => ({ ...current, [key]: value }))
    setMessage('입력 내용을 확인하고 있어요.')
  }

  const next = () => {
    if (!canContinue(step, values)) {
      setMessage('이 단계의 필수 항목을 선택해 주세요.')
      return
    }
    setMessage('좋아요. 다음 조건을 확인할게요.')
    setStep((current) => Math.min(current + 1, steps.length - 1))
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setIsAnalyzing(true)
    setMessage('입력한 조건을 기준으로 후보와 근거를 찾고 있어요.')
    window.setTimeout(() => onComplete(values), 900)
  }

  const selectLocation = (suggestion: LocationSuggestion) => {
    update('targetAreaInput', suggestion.value)
    setIsLocationListOpen(false)
    setMessage(`${suggestion.district}을(를) 희망 지역으로 선택했어요.`)
  }

  const updateFunds = (rawValue: string) => {
    const unit = moneyUnits.find((item) => item.label === fundUnit) ?? moneyUnits[0]
    const normalized = rawValue.replaceAll(',', '').replace(fundUnit === '억원' ? /[^0-9.]/g : /[^0-9]/g, '')
    const [integer = '', ...decimalParts] = normalized.split('.')
    const decimal = fundUnit === '억원' && decimalParts.length > 0 ? `.${decimalParts.join('').slice(0, 2)}` : ''
    const nextInput = `${integer}${decimal}`
    setFundInput(nextInput)
    if (!nextInput || nextInput === '.') {
      update('ownFundsKrw', '')
      return
    }
    update('ownFundsKrw', String(Math.round(Number(nextInput) * unit.multiplier)))
  }

  const changeFundUnit = (nextUnit: MoneyUnit) => {
    const next = moneyUnits.find((item) => item.label === nextUnit) ?? moneyUnits[0]
    setFundUnit(nextUnit)
    if (!values.ownFundsKrw) {
      setFundInput('')
      return
    }
    const converted = Number(values.ownFundsKrw) / next.multiplier
    setFundInput(String(Number(converted.toFixed(2))))
  }

  const handleLocationKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!isLocationListOpen || suggestedLocations.length === 0) return

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveLocationIndex((current) => (current + 1) % suggestedLocations.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveLocationIndex((current) => (current - 1 + suggestedLocations.length) % suggestedLocations.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      selectLocation(suggestedLocations[activeLocationIndex])
    } else if (event.key === 'Escape') {
      setIsLocationListOpen(false)
    }
  }

  if (isAnalyzing) {
    return (
      <main className="analysis-stage" aria-live="polite">
        <div className="analysis-stage__pulse" aria-hidden="true"><span /><span /><span /></div>
        <p className="stage-label">첫 분석</p>
        <h1>동네와 자금 조건을<br />함께 보고 있어요</h1>
        <p>가능한 자료의 범위와 기준일을 확인한 뒤, 검토할 가치가 있는 후보만 정리합니다.</p>
        <div className="analysis-checks" aria-label="분석 진행 항목">
          <span>지역 확인</span><span>비용 범위</span><span>운영 적합도</span>
        </div>
      </main>
    )
  }

  return (
    <div className="onboarding-shell">
      <header className="onboarding-nav">
        <a className="wordmark" href="#onboardingTop" aria-label="CaffeMate 온보딩 상단으로">CaffeMate</a>
        <span className="save-state">임시 입력 · 이 기기에만 표시</span>
      </header>

      <main className="onboarding" id="onboardingTop">
        <aside className="onboarding-progress" aria-label="온보딩 진행 상황">
          <p>첫 분석 준비</p>
          <ol>
            {steps.map((item, index) => (
              <li data-active={index === step} data-complete={index < step} key={item.short}>
                <button type="button" aria-label={`${index + 1}단계 ${item.short}`} disabled={index > step} onClick={() => setStep(index)}>
                  <span className="progress-number">{String(index + 1).padStart(2, '0')}</span><span className="progress-label">{item.short}</span>
                </button>
              </li>
            ))}
          </ol>
          <div className="progress-track" aria-hidden="true"><span style={{ transform: `scaleX(${progress / 100})` }} /></div>
        </aside>

        <form className="onboarding-form" onSubmit={submit} noValidate>
          <header className="stage-head">
            <p className="stage-label">{String(step + 1).padStart(2, '0')} · {steps[step].short}</p>
            <h1 aria-label={steps[step].question.join(' ')}>{steps[step].question.map((line) => <span aria-hidden="true" key={line}>{line}</span>)}</h1>
            <p>{steps[step].hint}</p>
          </header>

          <div className="stage-body" key={step}>
            {step === 0 && (
              <div className="field onboarding-field location-field" onFocusCapture={() => setIsLocationListOpen(true)} onBlurCapture={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setIsLocationListOpen(false)
              }}>
                <label htmlFor="targetArea">희망 지역</label>
                <div className="location-combobox">
                  <input
                    id="targetArea"
                    role="combobox"
                    aria-autocomplete="list"
                    aria-controls="locationSuggestions"
                    aria-expanded={isLocationListOpen && suggestedLocations.length > 0}
                    aria-activedescendant={isLocationListOpen && suggestedLocations.length > 0 ? `location-option-${activeLocationIndex}` : undefined}
                    value={values.targetAreaInput}
                    placeholder="예: 성수, 원천동, 수원 영통구"
                    onChange={(event) => {
                      update('targetAreaInput', event.target.value)
                      setActiveLocationIndex(0)
                      setIsLocationListOpen(true)
                    }}
                    onKeyDown={handleLocationKeyDown}
                    aria-describedby="targetAreaHelp"
                    aria-invalid={message.includes('필수') && !values.targetAreaInput.trim()}
                    required
                    autoFocus
                  />
                  {isLocationListOpen && suggestedLocations.length > 0 && (
                    <ul className="location-suggestions" id="locationSuggestions" role="listbox" aria-label="연관 지역">
                      {suggestedLocations.map((suggestion, index) => (
                        <li key={suggestion.value}>
                          <button
                            id={`location-option-${index}`}
                            role="option"
                            aria-selected={index === activeLocationIndex}
                            type="button"
                            onMouseDown={(event) => event.preventDefault()}
                            onMouseEnter={() => setActiveLocationIndex(index)}
                            onClick={() => selectLocation(suggestion)}
                          >
                            <strong>{suggestion.district}</strong>
                            <span>{suggestion.municipality}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <p id="targetAreaHelp">시·군·구와 동네 이름까지 적으면 지역을 더 정확히 찾을 수 있어요.</p>
              </div>
            )}

            {step === 1 && (
              <div className="stage-stack">
                <div className="field onboarding-field">
                  <label htmlFor="ownFunds">현재 자기자금</label>
                  <div className="money-input">
                    <input id="ownFunds" type="text" inputMode={fundUnit === '억원' ? 'decimal' : 'numeric'} value={formatMoneyInput(fundInput)} placeholder={(moneyUnits.find((item) => item.label === fundUnit) ?? moneyUnits[0]).placeholder} onChange={(event) => updateFunds(event.target.value)} aria-describedby="fundsHelp fundsConverted" required autoFocus />
                    <label className="sr-only" htmlFor="fundUnit">금액 단위</label>
                    <select id="fundUnit" aria-label="금액 단위" value={fundUnit} onChange={(event) => changeFundUnit(event.target.value as MoneyUnit)}>
                      {moneyUnits.map((unit) => <option value={unit.label} key={unit.label}>{unit.label}</option>)}
                    </select>
                  </div>
                  <p className="funds-converted" id="fundsConverted" aria-live="polite">{formatFundsSummary(values.ownFundsKrw)}</p>
                  <p id="fundsHelp">보증금과 초기 운영자금까지 포함해 실제로 사용할 수 있는 범위로 적어 주세요.</p>
                </div>
                <ChoiceGroup legend="대출도 고려하고 있나요?" name="borrowingIntent" value={values.borrowingIntent} onChange={(value) => update('borrowingIntent', value as OnboardingValues['borrowingIntent'])} options={[
                  { value: 'YES', label: '고려함', description: '가능한 대출 범위도 함께 봅니다.' },
                  { value: 'NO', label: '고려하지 않음', description: '자기자금 안에서만 후보를 찾습니다.' },
                  { value: 'UNDECIDED', label: '아직 미정', description: '미정 상태로 분석을 시작합니다.' },
                ]} />
              </div>
            )}

            {step === 2 && <ChoiceGroup legend="창업 유형" name="cafeTypePreference" value={values.cafeTypePreference} onChange={(value) => update('cafeTypePreference', value as OnboardingValues['cafeTypePreference'])} options={[
              { value: 'OPEN_TO_BOTH', label: '둘 다 비교', description: '개인카페와 프랜차이즈를 같은 축으로 비교합니다.' },
              { value: 'INDEPENDENT_ONLY', label: '개인카페', description: '검증 가능한 표준 운영 모델을 기준으로 봅니다.' },
              { value: 'FRANCHISE_ONLY', label: '프랜차이즈', description: '실제 개인 가맹 가능 여부를 먼저 확인합니다.' },
            ]} />}

            {step === 3 && <ChoiceGroup legend="운영 방식" name="operationMode" value={values.operationMode} onChange={(value) => update('operationMode', value as OnboardingValues['operationMode'])} options={[
              { value: 'DIRECT_FULL_TIME', label: '직접 전업 운영', description: '내가 주 운영자로 매장에 상주합니다.' },
              { value: 'DIRECT_PART_TIME', label: '시간제 참여', description: '직장이나 다른 일과 함께 운영합니다.' },
              { value: 'EMPLOYEE_LED', label: '직원 중심 운영', description: '직원이 운영하고 나는 관리에 집중합니다.' },
              { value: 'UNDECIDED', label: '아직 미정', description: '운영 방식이 정해지지 않은 상태도 유효합니다.' },
            ]} />}

            {step === 4 && (
              <div className="review-layout">
                <dl className="review-list">
                  <div><dt>희망 지역</dt><dd>{values.targetAreaInput}</dd><button type="button" onClick={() => setStep(0)}>수정</button></div>
                  <div><dt>자기자금</dt><dd>{formatKrw(values.ownFundsKrw)}</dd><button type="button" onClick={() => setStep(1)}>수정</button></div>
                  <div><dt>창업 유형</dt><dd>{values.cafeTypePreference ? cafeTypeLabels[values.cafeTypePreference] : '미정'}</dd><button type="button" onClick={() => setStep(2)}>수정</button></div>
                  <div><dt>운영 방식</dt><dd>{values.operationMode ? operationLabels[values.operationMode] : '미정'}</dd><button type="button" onClick={() => setStep(3)}>수정</button></div>
                </dl>
                <details className="optional-fields">
                  <summary>선택 정보도 추가할까요?</summary>
                  <div className="optional-fields__body">
                    <div className="field onboarding-field"><label htmlFor="openingPeriod">희망 개업 시기</label><input id="openingPeriod" value={values.desiredOpeningPeriod} placeholder="예: 2027년 상반기, 미정" onChange={(event) => update('desiredOpeningPeriod', event.target.value)} /></div>
                    <div className="field onboarding-field"><label htmlFor="experience">카페 운영 경험</label><input id="experience" value={values.priorCafeExperience} placeholder="예: 바리스타 2년, 경험 없음" onChange={(event) => update('priorCafeExperience', event.target.value)} /></div>
                  </div>
                </details>
                <div className="analysis-boundary"><strong>분석이 대신하지 않는 것</strong><p>계약, 결제, 대출 실행과 최종 창업 결정은 자동으로 진행하지 않습니다.</p></div>
              </div>
            )}
          </div>

          <footer className="stage-actions">
            <p role="status" data-tone={message.includes('필수') ? 'error' : undefined}>{message}</p>
            <div>
              {step > 0 && <button className="btn" type="button" onClick={() => setStep((current) => current - 1)}>이전</button>}
              {step < steps.length - 1 ? <button className="btn btn--primary" type="button" onClick={next}>다음</button> : <button className="btn btn--primary" type="submit">분석 시작</button>}
            </div>
          </footer>
        </form>
      </main>

      <footer className="onboarding-footer">
        <p>좋은 후보보다,<br />확인할 가치가 있는 후보부터.</p>
        <div><strong>CaffeMate</strong><span>카페 창업 검토를 위한 의사결정 도구</span></div>
      </footer>
    </div>
  )
}
