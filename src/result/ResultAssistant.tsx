import { type FormEvent, useEffect, useRef, useState } from "react";
import { waitForWorkflow, type ControlApiClient, type FeedbackPreview, type ResultCandidate, type ResultExplanation, type ResultView } from "../apiClient";
import { Badge, displayText, displayValue, explanationError, internalLabel, uniqueLabels, userError } from "../presentation";

function ConditionChangePanel({
  client,
  projectId,
  onResult,
  suggestion,
}: {
  client: ControlApiClient;
  projectId: string;
  onResult: (result: ResultView) => void;
  suggestion?: string;
}) {
  const [draft, setDraft] = useState("");
  const [preview, setPreview] = useState<FeedbackPreview | null>(null);
  const [status, setStatus] = useState(
    "예: 저가 브랜드는 제외하고 10평 이하로 다시 보고 싶어요.",
  );
  const [statusTone, setStatusTone] = useState<
    "idle" | "loading" | "error" | "success"
  >("idle");
  const [inputInvalid, setInputInvalid] = useState(false);
  const [busyAction, setBusyAction] = useState<
    "preview" | "cancel" | "confirm" | null
  >(null);
  const busy = busyAction !== null;
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!suggestion || preview || busy) return;
    const timer = window.setTimeout(() => {
      setDraft(suggestion);
      inputRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [suggestion, preview, busy]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim()) {
      setStatus("바꾸고 싶은 조건을 한 문장 이상 입력해 주세요.");
      setStatusTone("error");
      setInputInvalid(true);
      inputRef.current?.focus();
      return;
    }
    setInputInvalid(false);
    setBusyAction("preview");
    setStatus("변경안 미리보기를 만들고 있습니다.");
    setStatusTone("loading");
    try {
      const next = await client.createFeedbackPreview(projectId, draft.trim());
      setPreview(next);
      setStatus(
        next.status === "CLARIFICATION_REQUIRED"
          ? "질문을 확인한 뒤 입력 내용을 더 구체적으로 적어 주세요."
          : next.status === "REVIEW_REQUIRED"
            ? "변경안 미리보기를 만들었습니다. 적용 전 내용을 확인해 주세요."
            : `변경안 상태: ${internalLabel(next.status)}`,
      );
      setStatusTone(
        next.status === "REVIEW_REQUIRED" ? "success" : "idle",
      );
    } catch (error) {
      setStatus(userError(error, "변경안을 만들지 못했습니다."));
      setStatusTone("error");
    } finally {
      setBusyAction(null);
    }
  };

  const cancel = async () => {
    if (!preview) return;
    const needsRevision = preview.status !== "REVIEW_REQUIRED";
    setBusyAction("cancel");
    setStatus("변경안을 닫고 입력 화면으로 돌아가고 있습니다.");
    setStatusTone("loading");
    try {
      await client.cancelFeedback(projectId, preview.preview_id);
      setPreview(null);
      setStatus(
        needsRevision
          ? "입력을 수정할 수 있습니다. 현재 결과는 바뀌지 않았습니다."
          : "변경안을 취소했습니다. 현재 결과는 바뀌지 않았습니다.",
      );
      setStatusTone("idle");
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } catch (error) {
      setStatus(userError(error, "변경안을 취소하지 못했습니다."));
      setStatusTone("error");
    } finally {
      setBusyAction(null);
    }
  };

  const confirm = async () => {
    if (!preview?.proposal_digest) return;
    setBusyAction("confirm");
    setStatus("확인한 변경안을 반영하고 결과를 다시 계산하고 있습니다.");
    setStatusTone("loading");
    try {
      const resolution = await client.confirmFeedback(projectId, preview);
      if (resolution.workflow) {
        const progress = await waitForWorkflow(
          client,
          projectId,
          resolution.workflow,
          (next) =>
            setStatus(
              `결과 재계산 ${next.completed_stage_count}/${next.total_stage_count}`,
            ),
        );
        if (!["SUCCEEDED", "PARTIAL"].includes(progress.status))
          throw new Error(
            `재계산이 완료되지 않았습니다: ${internalLabel(progress.status)}`,
          );
        onResult(await client.getResult(projectId));
      }
      setDraft("");
      setPreview(null);
      setStatus("확인한 변경안을 반영하고 결과를 갱신했습니다.");
      setStatusTone("success");
    } catch (error) {
      setStatus(userError(error, "변경안을 반영하지 못했습니다."));
      setStatusTone("error");
    } finally {
      setBusyAction(null);
    }
  };

  const changedFields = preview
    ? Object.keys(preview.after_founder ?? {}).filter(
        (key) =>
          JSON.stringify(preview.before_founder[key]) !==
          JSON.stringify(preview.after_founder?.[key]),
      )
    : [];
  return (
    <div className="condition-change-panel">
      <div className="feedback-panel__head">
        <Badge tone="accent">변경 전 확인 필수</Badge>
        <h2 id="feedbackTitle">조건 변경 제안</h2>
        <p>
          바꾸고 싶은 조건을 문장으로 적어 주세요. 변경안을 먼저 보여드리고,
          확인한 뒤에만 결과에 반영해요.
        </p>
      </div>
      <form className="feedback-form" onSubmit={submit}>
        <div
          className="field"
          data-state={
            busyAction === "preview"
              ? "loading"
              : inputInvalid
                ? "error"
                : statusTone === "success"
                  ? "success"
                  : undefined
          }
        >
          <label htmlFor="feedbackInput">바꾸고 싶은 조건</label>
          <div className="feedback-compose">
            <textarea
              id="feedbackInput"
              ref={inputRef}
              value={draft}
              disabled={busy || preview !== null}
              aria-busy={busyAction === "preview" || undefined}
              aria-describedby="feedbackStatus"
              aria-invalid={inputInvalid || undefined}
              onChange={(event) => {
                setDraft(event.target.value);
                if (inputInvalid) {
                  setInputInvalid(false);
                  setStatus("입력한 조건으로 변경안 미리보기를 만들 수 있습니다.");
                  setStatusTone("idle");
                }
              }}
              placeholder="저가 브랜드는 제외하고 10평 이하로 보고 싶어요."
            />
            <button
              className="btn btn--primary"
              disabled={busy || preview !== null}
              aria-busy={busyAction === "preview" || undefined}
              type="submit"
            >
              {busyAction === "preview"
                ? "미리보기 만드는 중"
                : "변경안 미리보기"}
            </button>
          </div>
        </div>
      </form>
      {preview && (
        <section className="proposal" aria-labelledby="proposalTitle">
          <div className="proposal__head">
            <h3 id="proposalTitle">적용 전 변경 확인</h3>
            <p>
              상태: {internalLabel(preview.status)} · 아직 결과에는 반영되지
              않았습니다.
            </p>
          </div>
          {changedFields.map((field) => (
            <div className="diff-row" key={field}>
              <span className="diff-label">
                {internalLabel(field, "창업 조건")}
              </span>
              <div className="diff-values">
                <span className="diff-old">
                  {displayValue(preview.before_founder[field])}
                </span>
                <span>→</span>
                <span className="diff-new">
                  {displayValue(preview.after_founder?.[field])}
                </span>
              </div>
            </div>
          ))}
          {preview.clarifying_questions.map((question) => (
            <p key={question}>{displayText(question)}</p>
          ))}
          {preview.risk_flags.length > 0 && (
            <p>주의: {uniqueLabels(preview.risk_flags).join(" · ")}</p>
          )}
          <div className="feedback-actions">
            <button
              className="btn"
              disabled={busy}
              aria-busy={busyAction === "cancel" || undefined}
              type="button"
              onClick={cancel}
            >
              {busyAction === "cancel"
                ? "돌아가는 중"
                : preview.status === "REVIEW_REQUIRED"
                  ? "변경안 취소"
                  : "입력 다시 하기"}
            </button>
            <button
              className="btn btn--primary"
              disabled={
                busy ||
                preview.status !== "REVIEW_REQUIRED" ||
                !preview.proposal_digest
              }
              aria-busy={busyAction === "confirm" || undefined}
              type="button"
              onClick={confirm}
            >
              {busyAction === "confirm" ? "변경 적용 중" : "변경 적용"}
            </button>
          </div>
        </section>
      )}
      <p
        className="feedback-status"
        id="feedbackStatus"
        data-tone={statusTone === "idle" ? undefined : statusTone}
        aria-live="polite"
      >
        {status}
      </p>
      <p className="feedback-note">
        계약, 결제, 본사 연락은 자동으로 실행하지 않습니다.
      </p>
    </div>
  );
}

const EXPLANATION_SAMPLES = [
  "왜 이 안을 먼저 보나요?",
  "예산에서 가장 위험한 부분은 뭐예요?",
  "무엇이 바뀌면 판단이 달라지나요?",
] as const;

export function FeedbackPanel({
  client,
  projectId,
  result,
  candidate,
  onResult,
  suggestion,
}: {
  client: ControlApiClient;
  projectId: string;
  result: ResultView;
  candidate: ResultCandidate;
  onResult: (result: ResultView) => void;
  suggestion?: string;
}) {
  const [mode, setMode] = useState<"explain" | "condition">("explain");
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<
    Array<{ question: string; answer: ResultExplanation }>
  >([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(
    "궁금한 점을 고르거나 직접 입력해 주세요.",
  );
  const [statusTone, setStatusTone] = useState<
    "idle" | "loading" | "error" | "success"
  >("idle");
  const [invalid, setInvalid] = useState(false);
  const explanationInputRef = useRef<HTMLTextAreaElement>(null);
  const latestAnswerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!suggestion) return;
    const timer = window.setTimeout(() => setMode("condition"), 0);
    return () => window.clearTimeout(timer);
  }, [suggestion]);

  useEffect(() => {
    if (answers.length === 0) return;
    latestAnswerRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [answers.length]);

  // 추천 질문도 한 번의 선택으로 답을 받을 수 있어야 하며, 질문만으로 결과 상태를 바꾸지 않는다.
  const submitQuestion = async (rawQuestion: string) => {
    if (busy) return;
    const trimmed = rawQuestion.trim();
    if (!trimmed) {
      setInvalid(true);
      setStatus("궁금한 점을 한 문장 이상 입력해 주세요.");
      setStatusTone("error");
      explanationInputRef.current?.focus();
      return;
    }
    setInvalid(false);
    setBusy(true);
    setStatus("현재 결과와 근거를 확인하고 있어요.");
    setStatusTone("loading");
    try {
      const answer = await client.explainResult(
        projectId,
        result,
        trimmed,
        candidate.candidate_id,
      );
      setAnswers((current) => [...current, { question: trimmed, answer }]);
      setQuestion("");
      setStatus("답변을 확인했어요. 현재 결과는 바뀌지 않았습니다.");
      setStatusTone("success");
    } catch (error) {
      setStatus(explanationError(error));
      setStatusTone("error");
    } finally {
      setBusy(false);
    }
  };

  const ask = (event: FormEvent) => {
    event.preventDefault();
    void submitQuestion(question);
  };

  return (
    <section
      className="feedback-panel"
      id="resultFeedback"
      aria-labelledby={
        mode === "explain" ? "resultAssistantTitle" : "feedbackTitle"
      }
    >
      {mode === "explain" ? (
        <>
          <div className="feedback-panel__head">
            <Badge tone="accent">결과 설명</Badge>
            <h2 id="resultAssistantTitle">결과에 대해 물어보기</h2>
            <p>
              현재 결과와 확인된 근거 안에서 설명해 드려요. 질문만으로 조건이나
              결과가 바뀌지는 않아요.
            </p>
          </div>
          {answers.length > 0 && (
            <div className="feedback-thread" aria-label="결과 설명 대화">
              {answers.map((entry, index) => (
                <div
                  className="explanation-turn"
                  key={entry.answer.explanation_id}
                  ref={index === answers.length - 1 ? latestAnswerRef : undefined}
                >
                  <p className="chat-bubble chat-bubble--user">{entry.question}</p>
                  <article className="explanation-answer">
                    <strong>{displayText(entry.answer.conclusion)}</strong>
                    {entry.answer.reasons.length > 0 && (
                      <ul className="explanation-list">
                        {entry.answer.reasons.map((reason) => (
                          <li key={reason}>{displayText(reason)}</li>
                        ))}
                      </ul>
                    )}
                    {entry.answer.evidence.length > 0 && (
                      <div className="explanation-evidence">
                        <span>확인한 근거</span>
                        {entry.answer.evidence.map((evidence) => (
                          <div key={evidence.evidence_id}>
                            <strong>
                              {displayText(evidence.source_title ?? evidence.label)}
                            </strong>
                            {evidence.value && (
                              <span>{displayText(evidence.value)}</span>
                            )}
                            {evidence.source_ref && (
                              <a
                                href={evidence.source_ref}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`${displayText(evidence.source_title ?? evidence.label)} 근거 원문 보기`}
                              >
                                근거 원문 보기
                              </a>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {entry.answer.unknowns.length > 0 && (
                      <p className="explanation-caveat">
                        아직 확인할 정보:{" "}
                        {entry.answer.unknowns.map(displayText).join(" · ")}
                      </p>
                    )}
                    {entry.answer.decision_change_conditions.length > 0 && (
                      <p className="explanation-caveat">
                        판단이 달라질 조건:{" "}
                        {entry.answer.decision_change_conditions
                          .map(displayText)
                          .join(" · ")}
                      </p>
                    )}
                    {entry.answer.suggested_action === "OPEN_CONDITION_CHANGE" && (
                      <button
                        className="btn"
                        type="button"
                        onClick={() => setMode("condition")}
                      >
                        조건 변경으로 이동
                      </button>
                    )}
                  </article>
                </div>
              ))}
            </div>
          )}
          <div className="sample-row" aria-label="추천 질문">
            {EXPLANATION_SAMPLES.map((sample) => (
              <button
                className="sample-chip"
                disabled={busy}
                type="button"
                key={sample}
                onClick={() => {
                  setQuestion(sample);
                  setInvalid(false);
                  void submitQuestion(sample);
                }}
              >
                {sample}
              </button>
            ))}
          </div>
          <form className="feedback-form" onSubmit={ask}>
            <div className="field" data-state={busy ? "loading" : invalid ? "error" : undefined}>
              <label htmlFor="explanationInput">궁금한 점</label>
              <div className="feedback-compose">
                <textarea
                  id="explanationInput"
                  ref={explanationInputRef}
                  value={question}
                  disabled={busy}
                  aria-busy={busy || undefined}
                  aria-invalid={invalid || undefined}
                  aria-describedby="explanationStatus"
                  onChange={(event) => {
                    setQuestion(event.target.value);
                    if (invalid) setInvalid(false);
                  }}
                  placeholder="예: 이 후보가 제 예산에 맞나요?"
                />
                <button className="btn btn--primary" disabled={busy} aria-busy={busy || undefined} type="submit">
                  {busy ? "근거 확인 중" : "답변 보기"}
                </button>
              </div>
            </div>
          </form>
          <p
            className="feedback-status"
            id="explanationStatus"
            data-tone={statusTone === "idle" ? undefined : statusTone}
            aria-live="polite"
          >
            {status}
          </p>
          <button
            className="feedback-mode-link"
            id="conditionModeButton"
            type="button"
            onClick={() => setMode("condition")}
          >
            조건 바꾸기
          </button>
        </>
      ) : (
        <>
          <button
            className="feedback-mode-link"
            type="button"
            onClick={() => setMode("explain")}
          >
            결과 설명으로 돌아가기
          </button>
          <ConditionChangePanel
            client={client}
            projectId={projectId}
            onResult={onResult}
            suggestion={suggestion}
          />
        </>
      )}
    </section>
  );
}

