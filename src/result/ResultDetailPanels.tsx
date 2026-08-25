import type { Project, ResultCandidate } from "../apiClient";
import { Badge, capitalDecision, displayText, formatDataDate, formatRange, formatWon, internalLabel, isHttpSource, resultStatus, severityLabel, statusTone, uniqueLabels } from "../presentation";

export function FranchisePanel({ candidate }: { candidate: ResultCandidate }) {
  if (candidate.case_type !== "FRANCHISE" || !candidate.franchise)
    return (
      <>
        <header className="panel__header">
          <h2>개인카페 모델</h2>
          <p>이 후보에는 프랜차이즈 조건이 적용되지 않습니다.</p>
        </header>
        <article className="surface">
          <p>운영 모델: {candidate.display_name}</p>
          <p>
            조정된 조건:{" "}
            {candidate.independent_model?.adjusted_fields.length
              ? uniqueLabels(
                  candidate.independent_model.adjusted_fields,
                  "창업 조건",
                ).join(" · ")
              : "없음"}
          </p>
        </article>
      </>
    );
  return (
    <>
      <header className="panel__header">
        <h2>가맹 조건을 문서 기준으로 확인합니다</h2>
        <p>브랜드 존재 여부와 출점 가능 여부를 분리해서 보여줍니다.</p>
      </header>
      <article className="surface">
        <table className="data-table">
          <tbody>
            <tr>
              <th>브랜드</th>
              <td>{candidate.display_name}</td>
            </tr>
            <tr>
              <th>개인 가맹 여부</th>
              <td>
                <Badge
                  tone={
                    candidate.franchise.eligibility === "VERIFIED"
                      ? "success"
                      : "warning"
                  }
                >
                  {internalLabel(candidate.franchise.eligibility)}
                </Badge>
              </td>
            </tr>
            <tr>
              <th>희망 지역 출점</th>
              <td>
                <Badge
                  tone={
                    candidate.franchise.availability_status === "AVAILABLE"
                      ? "success"
                      : "warning"
                  }
                >
                  {internalLabel(candidate.franchise.availability_status)}
                </Badge>
              </td>
            </tr>
            <tr>
              <th>가맹 확인 근거</th>
              <td>{candidate.franchise.eligibility_evidence_refs.length}건</td>
            </tr>
            <tr>
              <th>정보공개서 근거</th>
              <td>{candidate.franchise.disclosure_evidence_refs.length}건</td>
            </tr>
          </tbody>
        </table>
      </article>
    </>
  );
}

export function FundsPanel({
  project,
  candidate,
}: {
  project: Project;
  candidate: ResultCandidate;
}) {
  const finance = candidate.financial_summary;
  const capital = capitalDecision(project, candidate);
  return (
    <>
      <header className="panel__header">
        <h2>내 자금과 필요한 비용을 함께 볼게요</h2>
        <p>
          기본 모델과 현재까지 확인된 자료를 함께 반영한 비용 범위입니다.
        </p>
      </header>
      <div className="section-stack">
        <div className="fund-total">
          <span>예상 초기 필요자금</span>
          <strong>{formatRange(finance.initial_cash)}</strong>
          <small>
            확인된 값과 확인 전 기본 가정을 구분하여 계산한 참고 범위입니다.
          </small>
        </div>
        {capital.ownFunds != null && (
          <article className="surface">
            <dl className="cost-list">
              <div className="cost-row">
                <dt>현재 자기자금</dt>
                <dd>{formatWon(capital.ownFunds)}</dd>
              </div>
              <div className="cost-row">
                <dt>최소 필요자금과 차이</dt>
                <dd>
                  {capital.minimumGap != null && capital.minimumGap > 0
                    ? `${formatWon(capital.minimumGap)} 부족`
                    : "최소 범위 충당 가능"}
                </dd>
              </div>
              {capital.baseGap != null && capital.baseGap > 0 && (
                <div className="cost-row">
                  <dt>기준 필요자금과 차이</dt>
                  <dd>{formatWon(capital.baseGap)} 부족</dd>
                </div>
              )}
            </dl>
          </article>
        )}
        <article className="surface">
          <dl className="cost-list">
            <div className="cost-row">
              <dt>월 고정비</dt>
              <dd>{formatRange(finance.monthly_fixed_cost)}</dd>
            </div>
            <div className="cost-row">
              <dt>월 손익분기 매출</dt>
              <dd>{formatWon(finance.break_even_monthly_sales_krw)}</dd>
            </div>
            <div className="cost-row">
              <dt>하루 필요한 주문</dt>
              <dd>
                {finance.required_daily_orders == null
                  ? "확인되지 않음"
                  : `${finance.required_daily_orders.toLocaleString("ko-KR")}건`}
              </dd>
            </div>
          </dl>
          <p className="table-note">
            손익분기 매출과 주문 수는 계산값이며, 이 동네에서 실제로 달성할 수
            있다는 뜻은 아닙니다.
          </p>
        </article>
        <article className="surface surface--flat">
          <div className="surface__head">
            <h3>비용 확인 상태</h3>
          </div>
          {finance.unknown_cost_fields.length ? (
            <ul className="plain-list plain-list--neutral">
              {uniqueLabels(finance.unknown_cost_fields, "추가 비용 항목").map(
                (field) => (
                  <li key={field}>{field}</li>
                ),
              )}
            </ul>
          ) : (
            <p>
              계산에 필요한 항목은 모두 채워졌어요. 실제 자료로 확인된 값은
              반영하고, 확인 전 값은 기본 가정으로 구분했어요.
            </p>
          )}
        </article>
      </div>
    </>
  );
}

export function RisksPanel({ candidate }: { candidate: ResultCandidate }) {
  const officialDocuments = candidate.official_documents ?? [];
  const officialDocumentGaps = candidate.official_document_gaps ?? [];
  const groupedRisks = Object.values(
    candidate.risks.reduce<
      Record<
        string,
        {
          severity: ResultCandidate["risks"][number]["severity"];
          summary: string;
          count: number;
        }
      >
    >((groups, risk) => {
      const summary = displayText(risk.summary);
      const key = `${risk.severity}:${summary}`;
      groups[key] = groups[key]
        ? { ...groups[key], count: groups[key].count + 1 }
        : { severity: risk.severity, summary, count: 1 };
      return groups;
    }, {}),
  );
  return (
    <>
      <header className="panel__header">
        <h2>판단을 뒤집을 조건부터 확인합니다</h2>
        <p>위험, 공식 문서, 다음 행동을 사용자 관점에서 정리합니다.</p>
      </header>
      <div className="section-stack">
        <div className="warning-box" role="note">
          <span aria-hidden="true">!</span>
          <p>
            <strong>이 결과는 최종 창업 결정을 대신하지 않습니다.</strong> 계약,
            송금, 대출과 최종 창업 여부는 사용자가 결정합니다.
          </p>
        </div>
        <article className="surface">
          <div className="surface__head">
            <h3>주요 위험</h3>
          </div>
          {groupedRisks.length ? (
            <ul className="plain-list">
              {groupedRisks.map((risk) => (
                <li key={`${risk.severity}-${risk.summary}`}>
                  <div>
                    <strong>
                      {severityLabel(risk.severity)}
                      {risk.count > 1 ? ` · ${risk.count}개 항목` : ""}
                    </strong>
                    <p>{risk.summary}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>등록된 위험이 없습니다.</p>
          )}
        </article>
        <article className="surface">
          <div className="surface__head">
            <h3>확인한 공식 문서</h3>
            <p>
              검색 결과 중 출처와 기준일을 확인하고 현재 근거 원장에 연결한
              문서만 표시합니다.
            </p>
          </div>
          {officialDocuments.length ? (
            <ul className="official-documents">
              {officialDocuments.map((document) => (
                <li key={`${document.document_version}-${document.source_ref}`}>
                  <div className="official-document__head">
                    <div>
                      <span>{document.purposes.join(" · ")}</span>
                      <strong>{displayText(document.title)}</strong>
                    </div>
                    <Badge
                      tone={
                        document.freshness_status === "FRESH"
                          ? "success"
                          : "warning"
                      }
                    >
                      {document.freshness_status === "FRESH"
                        ? "기준일 충족"
                        : "기준일 확인 필요"}
                    </Badge>
                  </div>
                  <p>{displayText(document.excerpt)}</p>
                  <div className="official-document__source">
                    <span>{formatDataDate(document.data_date)}</span>
                    {isHttpSource(document.source_ref) ? (
                      <a
                        href={document.source_ref}
                        target="_blank"
                        rel="noreferrer"
                      >
                        공식 원문 보기
                      </a>
                    ) : (
                      <span>공식 원문 주소 미확보</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              현재 결과에 연결할 수 있는 공식 문서를 확보하지 못했습니다. 검색
              결과가 없는 항목은 추정해서 채우지 않습니다.
            </p>
          )}
          {officialDocumentGaps.length > 0 && (
            <div className="document-gaps" role="note">
              <strong>아직 확보하지 못한 공식 문서</strong>
              <ul>
                {officialDocumentGaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </div>
          )}
        </article>
        <article className="surface">
          <div className="surface__head">
            <h3>판단 전환 조건</h3>
          </div>
          {candidate.counterfactuals.length ? (
            <ul className="plain-list plain-list--neutral">
              {candidate.counterfactuals.map((item) => (
                <li key={`${item.variable}-${item.condition}`}>
                  <div>
                    <strong>
                      {internalLabel(item.variable, "확인할 조건")}:{" "}
                      {displayText(item.condition)}
                    </strong>
                    <p>{displayText(item.decision_impact)}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>등록된 판단 전환 조건이 없습니다.</p>
          )}
        </article>
        <article className="surface">
          <div className="surface__head">
            <h3>다음 검증 행동</h3>
          </div>
          <ol className="condition-list">
            {candidate.next_actions.map((action) => (
              <li key={action}>
                <div>
                  <strong>{displayText(action)}</strong>
                </div>
              </li>
            ))}
          </ol>
        </article>
      </div>
    </>
  );
}

