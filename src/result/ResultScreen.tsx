import { useState } from "react";
import { waitForWorkflow, type CandidateSelection, type ControlApiClient, type PreparationGuide, type Project, type PropertyTermsInput, type ResultCandidate, type ResultView } from "../apiClient";
import { Badge, candidateSource, capitalDecision, displayText, formatRange, internalLabel, resultStatus, statusTone, userError } from "../presentation";
import { FeedbackPanel } from "./ResultAssistant";
import { MarketPanel, OverviewPanel, panels, ResultNav, type PanelName } from "./ResultOverviewPanels";
import { FranchisePanel, FundsPanel, RisksPanel } from "./ResultDetailPanels";
import { VerificationFlow, type PropertyRecalculation } from "../verification/VerificationFlow";

function ActivePanel({
  panel,
  project,
  candidate,
}: {
  panel: PanelName;
  project: Project;
  candidate: ResultCandidate;
}) {
  const content = {
    overview: <OverviewPanel project={project} candidate={candidate} />,
    market: <MarketPanel project={project} candidate={candidate} />,
    franchise: <FranchisePanel candidate={candidate} />,
    funds: <FundsPanel project={project} candidate={candidate} />,
    risks: <RisksPanel candidate={candidate} />,
  }[panel];
  return (
    <section
      className="panel"
      id={`panel-${panel}`}
      role="tabpanel"
      aria-labelledby={`tab-${panel}`}
      tabIndex={0}
    >
      {content}
    </section>
  );
}


export function ResultScreen({
  client,
  project,
  initialResult,
}: {
  client: ControlApiClient;
  project: Project;
  initialResult: ResultView;
}) {
  const [result, setResult] = useState(initialResult);
  const [activePanel, setActivePanel] = useState<PanelName>("overview");
  const [activeCandidateIndex, setActiveCandidateIndex] = useState(0);
  const [selection, setSelection] = useState<CandidateSelection | null>(null);
  const [actionStatus, setActionStatus] = useState(
    "결과를 확인하고 지금 상황에 맞는 다음 단계를 선택해 주세요.",
  );
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [preparationOpen, setPreparationOpen] = useState(false);
  const [preparationGuide, setPreparationGuide] =
    useState<PreparationGuide | null>(null);
  const [preparationBusy, setPreparationBusy] = useState(false);
  const [preparationError, setPreparationError] = useState("");
  const [feedbackSuggestion, setFeedbackSuggestion] = useState("");
  const candidates = result.candidates;
  const activeCandidate = candidates[activeCandidateIndex] ?? candidates[0];
  const noReviewable =
    result.outcome_status === "NO_REVIEWABLE_CANDIDATES" ||
    candidates.every((candidate) => candidate.review_status === "EXCLUDED");
  const capital = activeCandidate
    ? capitalDecision(project, activeCandidate)
    : null;
  const createdAt = new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(result.created_at));

  const loadPreparationGuide = async (nextSelection: CandidateSelection) => {
    setPreparationBusy(true);
    setPreparationError("");
    try {
      setPreparationGuide(
        await client.getPreparationGuide(
          project.project_id,
          nextSelection.selection_id,
        ),
      );
    } catch (error) {
      setPreparationError(
        userError(
          error,
          "공식 절차를 불러오지 못했습니다. 잠시 뒤 다시 확인해 주세요.",
        ),
      );
    } finally {
      setPreparationBusy(false);
    }
  };

  const select = async () => {
    setSelectionBusy(true);
    try {
      let selectionResult = result;
      let selectionCandidate = activeCandidate;
      if (selectionResult.freshness === "STALE") {
        setActionStatus("변경된 조건으로 창업안을 다시 계산하고 있어요.");
        const workflow = await client.startFirstProposal(project.project_id);
        const terminal = await waitForWorkflow(
          client,
          project.project_id,
          workflow,
          (progress) =>
            setActionStatus(
              `최신 조건 반영 ${progress.completed_stage_count}/${progress.total_stage_count}`,
            ),
        );
        if (terminal.status !== "SUCCEEDED") {
          throw new Error(
            "최신 조건 반영을 완료하지 못했습니다. 이 프로젝트에서 다시 시도해 주세요.",
          );
        }
        const refreshed = await client.getResult(project.project_id);
        const source = candidateSource(activeCandidate);
        const refreshedIndex = refreshed.candidates.findIndex(
          (candidate) => candidateSource(candidate) === source,
        );
        setResult(refreshed);
        setActivePanel("overview");
        if (refreshedIndex < 0) {
          setActiveCandidateIndex(0);
          throw new Error(
            "선택한 창업안은 최신 조건에서 제외됐습니다. 새 결과를 확인해 주세요.",
          );
        }
        setActiveCandidateIndex(refreshedIndex);
        selectionResult = refreshed;
        selectionCandidate = refreshed.candidates[refreshedIndex];
      }
      const next = await client.selectCandidate(
        project.project_id,
        selectionResult,
        selectionCandidate.candidate_id,
      );
      setSelection(next);
      setPreparationOpen(true);
      setPreparationGuide(null);
      setActionStatus(
        `창업안 선택을 완료했어요: ${selectionCandidate.display_name}. 이제 실제 점포 조건이나 문서를 추가해 보세요.`,
      );
      window.scrollTo({ top: 0 });
      void loadPreparationGuide(next);
    } catch (error) {
      setActionStatus(userError(error, "후보를 선택하지 못했습니다."));
    } finally {
      setSelectionBusy(false);
    }
  };
  const applyPropertyTerms = async (
    terms: PropertyTermsInput,
  ): Promise<PropertyRecalculation> => {
    if (!selection) throw new Error("선택한 창업안이 없습니다.");
    const application = await client.applyPropertyTerms(
      project.project_id,
      selection.selection_id,
      selection.selected_state_version,
      terms,
    );
    const terminal = await waitForWorkflow(
      client,
      project.project_id,
      application.recompute_workflow,
      (progress) =>
        setActionStatus(
          `점포 조건 재계산 ${progress.completed_stage_count}/${progress.total_stage_count}`,
        ),
    );
    if (terminal.status !== "SUCCEEDED")
      throw new Error("점포 조건 재계산을 완료하지 못했습니다.");
    const nextResult = await client.getResult(project.project_id);
    if (nextResult.freshness !== "CURRENT")
      throw new Error("점포 조건 반영 결과를 최신 상태로 저장하지 못했습니다.");
    const source = candidateSource(activeCandidate);
    const nextIndex = nextResult.candidates.findIndex(
      (nextCandidate) => candidateSource(nextCandidate) === source,
    );
    const nextCandidate = nextResult.candidates[nextIndex];
    if (!nextCandidate) throw new Error("재계산된 후보를 찾지 못했습니다.");
    setResult(nextResult);
    setActiveCandidateIndex(nextIndex);
    setSelection((current) =>
      current
        ? {
            ...current,
            selected_state_version: application.applied_state_version,
          }
        : current,
    );
    setActionStatus("점포 조건을 반영해 비용을 다시 계산했습니다.");
    return { mode: "LIVE", application, candidate: nextCandidate };
  };
  const updateResult = (next: ResultView) => {
    setResult(next);
    setActiveCandidateIndex(0);
    setActivePanel("overview");
    setSelection(null);
    setPreparationOpen(false);
    setPreparationGuide(null);
  };
  const refreshAfterDocument = async () => {
    const nextResult = await client.getResult(project.project_id);
    if (nextResult.freshness !== "CURRENT")
      throw new Error("문서 반영 결과를 최신 상태로 저장하지 못했습니다.");
    const source = candidateSource(activeCandidate);
    const nextIndex = nextResult.candidates.findIndex(
      (nextCandidate) => candidateSource(nextCandidate) === source,
    );
    setResult(nextResult);
    setActiveCandidateIndex(Math.max(0, nextIndex));
    setSelection((current) =>
      current
        ? {
            ...current,
            selected_state_version: nextResult.current_head.state_version,
          }
        : current,
    );
    const excluded = nextResult.candidates.every(
      (candidate) => candidate.review_status === "EXCLUDED",
    );
    setActionStatus(
      excluded
        ? "문서 조건을 반영하니 현재 예산에는 맞지 않았어요. 조건을 바꾸어 다시 비교할 수 있어요."
        : "문서에서 확인한 값으로 창업안을 다시 계산했습니다.",
    );
  };
  if (!activeCandidate)
    return (
      <main className="analysis-stage">
        <h1>분석할 창업안을 만들지 못했어요</h1>
        <p>희망 지역과 자금 조건을 확인한 뒤 다시 분석해 주세요.</p>
      </main>
    );

  if (selection && preparationOpen)
    return (
      <VerificationFlow
        client={client}
        projectId={project.project_id}
        candidate={
          candidates.find(
            (candidate) => candidate.candidate_id === selection.candidate_id,
          ) ?? activeCandidate
        }
        selection={selection}
        guide={preparationGuide}
        busy={preparationBusy}
        error={preparationError}
        onRetry={() => void loadPreparationGuide(selection)}
        onBack={() => {
          setPreparationOpen(false);
          window.scrollTo({ top: 0 });
        }}
        onApply={applyPropertyTerms}
        onDocumentApplied={refreshAfterDocument}
      />
    );

  return (
    <div className="shell min-h-dvh">
      <header className="topbar">
        <a className="wordmark" href="#top">
          CaffeMate
        </a>
        <div className="topbar__meta">
          <Badge tone={result.freshness === "CURRENT" ? "success" : "warning"}>
            {internalLabel(result.freshness)}
          </Badge>
          <span className="version">결과 생성 {createdAt}</span>
        </div>
      </header>
      <main className="page" id="top">
        {candidates.length > 1 && (
          <section
            className="candidate-picker"
            aria-labelledby="candidatePickerTitle"
          >
            <div className="candidate-picker__head">
              <div>
                <p className="candidate-picker__count">
                  {noReviewable ? "조건 변경이 필요한 안" : "검토 후보"} {candidates.length}개
                </p>
                <h2 id="candidatePickerTitle">
                  {noReviewable ? "현재 조건에서 어려운 이유를 확인하세요" : "추천안부터 살펴보세요"}
                </h2>
              </div>
              <p>모든 후보를 같은 자금과 운영 기준으로 비교했어요.</p>
            </div>
            <div
              className="candidate-tabs"
              role="tablist"
              aria-label="창업안 후보"
            >
              {candidates.map((candidate, index) => (
                <button
                  id={`candidate-tab-${index}`}
                  className="candidate-tab"
                  type="button"
                  role="tab"
                  aria-selected={activeCandidateIndex === index}
                  aria-controls="candidate-report"
                  tabIndex={activeCandidateIndex === index ? 0 : -1}
                  data-recommended={
                    candidate.is_primary_next_review || undefined
                  }
                  key={candidate.candidate_id}
                  onClick={() => {
                    setActiveCandidateIndex(index);
                    setActivePanel("overview");
                  }}
                >
                  <span className="candidate-tab__number">
                    {candidate.rank ? `${candidate.rank}순위` : "현재 제외"}
                  </span>
                  <strong>{candidate.display_name}</strong>
                  <small>
                    {resultStatus(project, candidate)} ·{" "}
                    {formatRange(candidate.financial_summary.initial_cash)}
                  </small>
                </button>
              ))}
            </div>
          </section>
        )}
        <section className="intro" aria-labelledby="pageTitle">
          <div className="intro__copy">
            <div className="context-line">
              <Badge>
                {activeCandidate.case_type === "FRANCHISE"
                  ? "프랜차이즈"
                  : "개인카페"}
              </Badge>
              <Badge
                tone={
                  capital?.needsPlanChange
                    ? "warning"
                    : statusTone(activeCandidate.review_status)
                }
              >
                {resultStatus(project, activeCandidate)}
              </Badge>
            </div>
            <h1 id="pageTitle">{activeCandidate.display_name}</h1>
            <p className="intro__lede">
              {activeCandidate.review_status === "EXCLUDED"
                ? "문서에서 확인한 점포 비용이 현재 자금 조건을 넘었어요. 비용이나 운영 규모를 바꾸어 다시 비교해 보세요."
                : capital?.needsPlanChange
                ? "지금 예산에 맞는 운영안이나 실제 점포 비용으로 한 번 더 비교해 보세요."
                : displayText(activeCandidate.summary)}
            </p>
          </div>
          {result.freshness === "STALE" && (
            <div className="demo-notice" role="note">
              <span className="demo-notice__mark">!</span>
              <p>
                입력 조건이 바뀌었어요. 이 안을 선택하면 최신 조건으로 다시
                계산한 뒤 같은 프로젝트에서 계속 검토할 수 있어요.
              </p>
            </div>
          )}
        </section>
        <div className="mobile-switcher">
          <label htmlFor="sectionSelect">결과 항목</label>
          <select
            className="section-select"
            id="sectionSelect"
            value={activePanel}
            onChange={(event) =>
              setActivePanel(event.target.value as PanelName)
            }
          >
            {panels.map((panel) => (
              <option value={panel.id} key={panel.id}>
                {panel.label}
              </option>
            ))}
          </select>
        </div>
        <div className="workbench">
          <aside className="rail">
            <div className="rail__inner">
              <FeedbackPanel
                key={`${result.result_bundle_id}-${activeCandidate.candidate_id}`}
                client={client}
                projectId={project.project_id}
                result={result}
                candidate={activeCandidate}
                onResult={updateResult}
                suggestion={feedbackSuggestion}
              />
              <ResultNav activePanel={activePanel} onChange={setActivePanel} />
            </div>
          </aside>
          <div className="panels" id="candidate-report">
            <ActivePanel
              panel={activePanel}
              project={project}
              candidate={activeCandidate}
              key={`${activeCandidate.candidate_id}-${activePanel}`}
            />
            <aside className="action-dock">
              <p className="action-dock__status" aria-live="polite">
                {actionStatus}
              </p>
              <div className="action-group">
                {selection ? (
                  <>
                    <button
                      className="btn btn--primary"
                      type="button"
                      onClick={() => {
                        setPreparationOpen(true);
                        window.scrollTo({ top: 0 });
                      }}
                    >
                      준비 자료 보기
                    </button>
                    <button
                      className="btn btn--accent"
                      type="button"
                      onClick={() => {
                        document.getElementById("conditionModeButton")?.click();
                        window.setTimeout(() => document.getElementById("feedbackInput")?.focus(), 0);
                      }}
                    >
                      조건 바꾸기
                    </button>
                  </>
                ) : activeCandidate.review_status === "EXCLUDED" ? (
                  <button
                    className="btn btn--primary"
                    onClick={() => {
                      document.getElementById("conditionModeButton")?.click();
                      setFeedbackSuggestion(
                        "현재 자기자금에 맞도록 점포 비용이나 카페 규모를 줄인 안으로 다시 보고 싶어요.",
                      );
                      setActionStatus(
                        "바꿀 조건을 준비했어요. 내용을 확인한 뒤 다시 비교해 주세요.",
                      );
                      window.setTimeout(
                        () => document.getElementById("feedbackInput")?.focus(),
                        0,
                      );
                    }}
                  >
                    조건 바꾸어 다시 비교하기
                  </button>
                ) : capital?.needsPlanChange ? (
                  <>
                    <button
                      className="btn btn--primary"
                      onClick={() => {
                        document.getElementById("conditionModeButton")?.click();
                        setFeedbackSuggestion(
                          "현재 자기자금 범위에 더 가까운 작은 개인카페 운영안으로 다시 보고 싶어요.",
                        );
                        setActionStatus(
                          "피드백 내용을 준비했어요. 확인한 뒤 제안을 만들어 주세요.",
                        );
                        window.setTimeout(
                          () =>
                            document.getElementById("feedbackInput")?.focus(),
                          0,
                        );
                      }}
                    >
                      예산에 맞는 작은 안 보기
                    </button>
                    <button
                      className="btn btn--accent"
                      disabled={selectionBusy}
                      onClick={select}
                    >
                      {selectionBusy ? "선택 중" : "이 안을 계속 검토하기"}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="btn btn--primary"
                      disabled={selectionBusy}
                      onClick={select}
                    >
                      {selectionBusy ? "선택 중" : "이 안을 계속 검토하기"}
                    </button>
                    <button
                      className="btn btn--accent"
                      onClick={() => {
                        document.getElementById("conditionModeButton")?.click();
                        window.setTimeout(() => document.getElementById("feedbackInput")?.focus(), 0);
                      }}
                    >
                      조건 바꾸기
                    </button>
                  </>
                )}
              </div>
              {selection && (
                <p className="table-note">
                  {`${
                    candidates.find(
                      (candidate) =>
                        candidate.candidate_id === selection.candidate_id,
                    )?.display_name ?? "선택한 창업안"
                  }의 준비 자료를 확인할 수 있어요.`}
                </p>
              )}
            </aside>
          </div>
        </div>
      </main>
      <footer className="footer">
        <strong>CaffeMate</strong>
        <span>계약과 최종 창업 결정을 대신하지 않습니다.</span>
        <span>현재 입력과 확인된 자료를 기준으로 계산했어요.</span>
      </footer>
    </div>
  );
}

