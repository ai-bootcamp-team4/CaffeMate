import type { ResultCandidate } from '../apiClient'
import { formatRange, formatWon, internalLabel, isHttpSource } from '../presentation'
import { decisionInputLabel, decisionInputValue, financeInputs, limitationCopy, provenanceLabel } from './resultPresentation'

export function FinanceBreakdown({ candidate }: { candidate: ResultCandidate }) {
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
