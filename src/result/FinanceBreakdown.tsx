import type { DecisionInput, ResultCandidate } from '../apiClient'
import { formatRange, formatWon, internalLabel, isHttpSource } from '../presentation'
import { decisionInputLabel, decisionInputValue, financeInputs, limitationCopy, provenanceLabel } from './resultPresentation'

function refinementLabel(input: DecisionInput) {
  const action = input.resolution_action
  if (!action) return null
  if (action.type !== 'PROPERTY_TERMS' && ['USER_CONFIRMED_FACT', 'RESOLVED_FACT'].includes(input.resolution_status)) return null
  if (action.type === 'PROPERTY_TERMS') return '실제 매물로 바꾸기'
  if (action.type === 'DOCUMENT_INTAKE') {
    const types = action.accepted_document_types ?? []
    if (types.includes('EQUIPMENT_QUOTE')) return '장비 견적 반영하기'
    if (types.includes('INTERIOR_QUOTE')) return '인테리어 견적 반영하기'
    if (input.field.toLowerCase().includes('royalty')) return '로열티 문서 반영하기'
    if (types.includes('FRANCHISE_DISCLOSURE') || types.includes('FRANCHISE_AGREEMENT')) return '가맹비 문서 반영하기'
    return '문서 값 반영하기'
  }
  if (action.type === 'USER_INPUT') return '직접 값 입력하기'
  return null
}

export function FinanceBreakdown({ candidate, onRefine, busy = false }: {
  candidate: ResultCandidate
  onRefine?: (input: DecisionInput) => void
  busy?: boolean
}) {
  const inputs = financeInputs(candidate)
  return (
    <section className="result-section" aria-labelledby="financeBreakdownTitle">
      <header className="result-section__head">
        <p className="result-kicker">판정에 실제 사용</p>
        <h2 id="financeBreakdownTitle">돈이 어떻게 계산됐나요?</h2>
        <p>실제 입력, 공식 참고값, 가정을 숫자 바로 옆에서 구분합니다.</p>
      </header>
      <div className="finance-summary-grid">
        <article><span>초기 필요자금</span><strong>{formatRange(candidate.financial_summary.initial_cash)}</strong></article>
        <article><span>월 고정비</span><strong>{formatRange(candidate.financial_summary.monthly_fixed_cost)}</strong></article>
        <article><span>손익분기 월매출</span><strong>{formatWon(candidate.financial_summary.break_even_monthly_sales_krw)}</strong><small>계산값이며 실제 달성 가능 매출 예측이 아닙니다.</small></article>
        <article><span>하루 필요 주문</span><strong>{candidate.financial_summary.required_daily_orders == null ? '계산되지 않음' : `${candidate.financial_summary.required_daily_orders.toLocaleString('ko-KR')}건`}</strong><small>손익분기 계산을 주문 수로 환산한 값입니다.</small></article>
      </div>
      {inputs.length ? (
        <div className="provenance-list">
          {inputs.map((input) => {
            const limitation = limitationCopy(input.limitation_code)
            return (
              <article className="provenance-row" key={input.field}>
                <div>
                  <span className="provenance-chip">{provenanceLabel(input)}</span>
                  <strong>{decisionInputLabel(input)}</strong>
                  <p>{decisionInputValue(input)}</p>
                </div>
                <div className="provenance-source">
                  {input.source ? (
                    <>
                      <span>{input.source.title}</span>
                      <small>{[input.source.data_date, input.source.geographic_scope].filter(Boolean).join(' · ')}</small>
                      {(input.source.filename || input.source.page_index != null || input.source.section_path) && (
                        <small>{[
                          input.source.filename,
                          input.source.page_index != null ? `${input.source.page_index + 1}페이지` : null,
                          input.source.section_path,
                        ].filter(Boolean).join(' · ')}</small>
                      )}
                      {input.source.source_ref && isHttpSource(input.source.source_ref) && <a href={input.source.source_ref} target="_blank" rel="noreferrer">원문 보기</a>}
                    </>
                  ) : <span>사용자 입력 또는 등록된 모델 값</span>}
                  {input.applied_to.length > 0 && <small>적용: {input.applied_to.map((target) => internalLabel(target, '계산 항목')).join(' · ')}</small>}
                  {limitation && <small>{limitation}</small>}
                  {refinementLabel(input) && onRefine && (
                    <button className="btn btn--accent finance-refine-action" disabled={busy} type="button" onClick={() => onRefine(input)}>
                      {refinementLabel(input)}
                    </button>
                  )}
                </div>
              </article>
            )
          })}
        </div>
      ) : (
        <p className="contract-gap">총액은 계산됐지만 항목별 provenance가 아직 공개 응답에 포함되지 않았습니다.</p>
      )}
    </section>
  )
}
