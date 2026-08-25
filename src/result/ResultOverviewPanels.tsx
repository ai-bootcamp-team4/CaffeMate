import type { KeyboardEvent } from "react";
import type { Project, ResultCandidate } from "../apiClient";
import { Badge, capitalDecision, displayText, formatDataDate, formatRange, formatWon, internalLabel, isHttpSource, marketSignalLabel, marketSignalValue, resultStatus, severityLabel, statusTone } from "../presentation";

export type PanelName = "overview" | "market" | "franchise" | "funds" | "risks";
export const panels: Array<{ id: PanelName; label: string }> = [
  { id: "overview", label: "판단 요약" },
  { id: "market", label: "상권 신호" },
  { id: "franchise", label: "가맹 조건" },
  { id: "funds", label: "필요자금" },
  { id: "risks", label: "위험과 검증" },
];

export function ResultNav({
  activePanel,
  onChange,
}: {
  activePanel: PanelName;
  onChange: (panel: PanelName) => void;
}) {
  const onKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let nextIndex = index;
    if (event.key === "ArrowDown") nextIndex = (index + 1) % panels.length;
    if (event.key === "ArrowUp")
      nextIndex = (index - 1 + panels.length) % panels.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = panels.length - 1;
    onChange(panels[nextIndex].id);
    window.setTimeout(
      () => document.getElementById(`tab-${panels[nextIndex].id}`)?.focus(),
      0,
    );
  };

  return (
    <nav
      className="result-nav"
      role="tablist"
      aria-orientation="vertical"
      aria-label="결과 상세 항목"
    >
      <p className="rail__caption">결과 상세</p>
      {panels.map((panel, index) => (
        <button
          className="tab-button"
          role="tab"
          id={`tab-${panel.id}`}
          aria-controls={`panel-${panel.id}`}
          aria-selected={activePanel === panel.id}
          tabIndex={activePanel === panel.id ? 0 : -1}
          key={panel.id}
          onClick={() => onChange(panel.id)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {panel.label}
        </button>
      ))}
    </nav>
  );
}

export function OverviewPanel({
  project,
  candidate,
}: {
  project: Project;
  candidate: ResultCandidate;
}) {
  const capital = capitalDecision(project, candidate);
  const excluded = candidate.review_status === "EXCLUDED";
  return (
    <>
      <header className="panel__header">
        <h2>
          {excluded
            ? "문서 조건을 반영하니 지금 예산에는 맞지 않아요"
            : capital.needsPlanChange
            ? "지금 예산에서 가능한 방법부터 찾아볼게요"
            : "지금 조건에서 살펴볼 만한 안이에요"}
        </h2>
        <p>
          카페 창업, 감이 아닌 데이터로 시작하세요. 현재 자금과 필요한 비용을
          먼저 비교했습니다.
        </p>
      </header>
      <div className="section-stack">
        <div className="judgement">
          <div className="judgement__status">
            <Badge
              tone={
                capital.needsPlanChange
                  ? "warning"
                  : statusTone(candidate.review_status)
              }
            >
              {resultStatus(project, candidate)}
            </Badge>
            <strong>{candidate.display_name}</strong>
            <p>
              {excluded && capital.minimumGap != null
                ? `확인한 점포 조건으로 계산하면 최소 ${formatWon(capital.minimumGap)}이 부족해요. 규모나 점포 조건을 바꾸어 다시 비교해 보세요.`
                : excluded
                  ? "확인한 문서 조건이 현재 자금이나 필수 조건과 맞지 않아요. 조건을 바꾸어 다시 비교해 보세요."
                : capital.needsPlanChange && capital.minimumGap != null
                ? `최소 ${formatWon(capital.minimumGap)}을 더 마련하거나, 카페 규모와 비용을 줄여야 해요.`
                : displayText(candidate.summary)}
            </p>
          </div>
          <div className="judgement__aside">
            <strong>가장 먼저 볼 숫자</strong>
            {capital.ownFunds != null && (
              <span>현재 자기자금 {formatWon(capital.ownFunds)}</span>
            )}
            {capital.minimumRequired != null && (
              <span>최소 필요자금 {formatWon(capital.minimumRequired)}</span>
            )}
            {capital.minimumGap != null && capital.minimumGap > 0 && (
              <span>최소 부족액 {formatWon(capital.minimumGap)}</span>
            )}
          </div>
        </div>
        {(excluded || capital.needsPlanChange) && (
          <div className="decision-note" role="note">
            <strong>다른 카페안까지 모두 어렵다는 뜻은 아니에요.</strong>
            <p>
              {`현재 선택한 창업안은 ${candidate.display_name}입니다.`} 이 안을
              자기자금만으로 시작하기 어렵다는 뜻입니다. 더 작은 운영안이나
              실제 점포 비용으로 다시 비교할 수 있어요.
            </p>
          </div>
        )}
        <article className="surface">
          <div className="surface__head">
            <h3>이번 계산에 사용한 정보</h3>
            <p>공식 자료와 아직 확인이 필요한 기본 가정을 나누어 보여드려요.</p>
          </div>
          <dl className="summary-grid">
            <div className="summary-item">
              <dt>유형</dt>
              <dd>
                {candidate.case_type === "FRANCHISE"
                  ? "프랜차이즈"
                  : "개인카페"}
              </dd>
            </div>
            <div className="summary-item">
              <dt>초기 필요자금</dt>
              <dd>{formatRange(candidate.financial_summary.initial_cash)}</dd>
              <small>현재는 기본 운영 모델을 포함한 참고 범위입니다.</small>
            </div>
            <div className="summary-item">
              <dt>연결된 자료</dt>
              <dd>{candidate.evidence_refs.length}건</dd>
              <small>상권과 후보 판단에 연결된 자료입니다.</small>
            </div>
            <div className="summary-item">
              <dt>확인 전 기본 가정</dt>
              <dd>{candidate.assumption_refs?.length ?? 0}건</dd>
              <small>실제 점포와 견적이 들어오면 교체됩니다.</small>
            </div>
          </dl>
        </article>
        <article className="surface">
          <div className="surface__head">
            <h3>정확도를 높이려면</h3>
          </div>
          {candidate.missing_fields.length ? (
            <ul className="plain-list">
              {candidate.missing_fields.map((item) => (
                <li key={item.field}>
                  <div>
                    <strong>
                      {internalLabel(item.field, "추가 확인 항목")}
                    </strong>
                    <p>
                      {displayText(item.impact)} 다음 확인:{" "}
                      {displayText(item.next_check)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              계산에 필요한 기본값은 채워졌어요. 다만 실제 점포의
              보증금·월세·권리금과 공사·장비 견적을 확인해야 최종 비용에
              가까워집니다.
            </p>
          )}
        </article>
      </div>
    </>
  );
}

export function MarketPanel({
  project,
  candidate,
}: {
  project: Project;
  candidate: ResultCandidate;
}) {
  const area = project.state?.area;
  const signals = candidate.market_signals ?? [];
  return (
    <>
      <header className="panel__header">
        <h2>이 동네에서 확인한 상권 정보예요</h2>
        <p>
          확인된 자료만 보여드리며, 동네 전체 수치를 내 점포의 예상매출로 바꾸지
          않습니다.
        </p>
      </header>
      <div className="section-stack">
        <article className="surface">
          <div className="surface__head">
            <h3>{area?.display_name ?? "희망 지역 확인 중"}</h3>
            <p>현재 결과에 실제로 연결된 상권 자료를 기준으로 살펴봅니다.</p>
          </div>
          <table className="data-table">
            <tbody>
              <tr>
                <th>지역 확인</th>
                <td>
                  {internalLabel(area?.resolution_status ?? "UNRESOLVED")}
                </td>
              </tr>
              <tr>
                <th>확인한 상권 지표</th>
                <td>{signals.length}개</td>
              </tr>
              <tr>
                <th>후보 판단 연결 자료</th>
                <td>{candidate.evidence_refs.length}건</td>
              </tr>
              <tr>
                <th>실제 점포 자료</th>
                <td>아직 없음</td>
              </tr>
            </tbody>
          </table>
        </article>
        <article className="surface">
          <div className="surface__head">
            <h3>확인된 상권 수치</h3>
            <p>후보 판단에 실제로 연결된 자료만 표시합니다.</p>
          </div>
          {signals.length ? (
            <ul className="market-signals">
              {signals.map((signal) => (
                <li key={signal.evidence_id}>
                  <div className="market-signal__value">
                    <span>{marketSignalLabel(signal)}</span>
                    <strong>{marketSignalValue(signal)}</strong>
                  </div>
                  <p>{signal.caveat}</p>
                  <div className="market-signal__source">
                    <Badge
                      tone={
                        signal.freshness_status === "FRESH"
                          ? "success"
                          : "warning"
                      }
                    >
                      {signal.freshness_status === "FRESH"
                        ? "최신 기준 충족"
                        : "기준일 확인 필요"}
                    </Badge>
                    <span>{formatDataDate(signal.data_date)}</span>
                    {isHttpSource(signal.source_ref) ? (
                      <a
                        href={signal.source_ref}
                        target="_blank"
                        rel="noreferrer"
                      >
                        공식 원문 보기
                      </a>
                    ) : (
                      <span>출처: {displayText(signal.source_title)}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              현재 후보에 연결된 상권 수치가 없습니다. 확인되지 않은 값은
              추정해서 채우지 않습니다.
            </p>
          )}
          <p className="table-note">
            상권 평균이나 추정치는 개별 점포의 실제 매출이 아닙니다. 실제 점포를
            정하면 임대 조건과 위치를 함께 다시 확인해야 해요.
          </p>
        </article>
      </div>
    </>
  );
}

