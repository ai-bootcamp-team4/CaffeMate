import { type FormEvent, useState } from "react";
import { DocumentIntake } from "../DocumentIntake";
import { PreparationProcedures } from "../PreparationProcedures";
import type { CandidateSelection, ControlApiClient, PreparationGuide, PropertyTermsApplication, PropertyTermsInput, ResultCandidate } from "../apiClient";
import { Badge, formatWon } from "../presentation";

export type PropertyRecalculation =
  | {
      mode: "LIVE";
      application: PropertyTermsApplication;
      candidate: ResultCandidate;
    }
  | { mode: "DEMO"; initialPropertyCost: number; monthlyOccupancyCost: number };

const demoPropertyTerms = {
  address: "서울 마포구 공덕동 데모 점포 · 실매물 아님",
  area_sqm: "33",
  deposit_manwon: "3000",
  monthly_rent_manwon: "220",
  management_fee_manwon: "20",
  key_money_manwon: "1000",
};

export function VerificationFlow({
  client,
  projectId,
  candidate,
  selection,
  guide,
  busy,
  error,
  onRetry,
  onBack,
  onApply,
  onDocumentApplied,
}: {
  client: ControlApiClient;
  projectId: string;
  candidate: ResultCandidate;
  selection: CandidateSelection;
  guide: PreparationGuide | null;
  busy: boolean;
  error: string;
  onRetry: () => void;
  onBack: () => void;
  onApply: (terms: PropertyTermsInput) => Promise<PropertyRecalculation>;
  onDocumentApplied: () => Promise<void>;
}) {
  const [values, setValues] = useState(demoPropertyTerms);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(
    "데모 예시를 불러오거나 점포 조건을 직접 입력해 주세요.",
  );
  const [outcome, setOutcome] = useState<PropertyRecalculation | null>(null);
  const setValue = (key: keyof typeof demoPropertyTerms, value: string) =>
    setValues((current) => ({ ...current, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setStatus("입력한 점포 조건으로 비용을 다시 계산하고 있어요.");
    try {
      const next = await onApply({
        address: values.address.trim(),
        area_sqm: Number(values.area_sqm),
        floor: null,
        deposit_krw: Number(values.deposit_manwon) * 10_000,
        monthly_rent_krw: Number(values.monthly_rent_manwon) * 10_000,
        management_fee_krw: Number(values.management_fee_manwon) * 10_000,
        key_money_krw:
          values.key_money_manwon === ""
            ? null
            : Number(values.key_money_manwon) * 10_000,
      });
      setOutcome(next);
      setStatus("점포 조건을 반영해 비용을 다시 계산했습니다.");
    } catch {
      setOutcome({
        mode: "DEMO",
        initialPropertyCost:
          (Number(values.deposit_manwon) +
            Number(values.key_money_manwon || 0)) *
          10_000,
        monthlyOccupancyCost:
          (Number(values.monthly_rent_manwon) +
            Number(values.management_fee_manwon)) *
          10_000,
      });
      setStatus(
        "운영 재계산을 완료하지 못해 입력값 기준 데모 계산을 보여드려요.",
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="shell min-h-dvh">
      <header className="topbar">
        <a className="wordmark" href="#preparationTop">
          CaffeMate
        </a>
        <div className="topbar__meta">
          <Badge tone="success">검토 대상 선택됨</Badge>
        </div>
      </header>
      <main className="page preparation-page" id="preparationTop">
        <header className="preparation-hero">
          <div>
            <p className="candidate-picker__count">선택한 창업안</p>
            <h1>{candidate.display_name}에 점포 조건을 넣어보세요</h1>
            <p>
              임시 범위로 계산한 창업비를 실제로 알아본
              보증금·월세·관리비·권리금으로 바꿔 비교해요.
            </p>
          </div>
          <button className="btn btn--accent" type="button" onClick={onBack}>
            결과 비교로 돌아가기
          </button>
        </header>
        <div className="preparation-layout">
          <section className="preparation-main">
            <article className="surface" aria-labelledby="propertyTermsTitle">
              <div className="surface__head property-form__head">
                <div>
                  <h2 id="propertyTermsTitle">점포 조건 입력</h2>
                  <p>
                    아직 점포가 없다면 데모 예시로 재계산 흐름을 먼저 확인할 수
                    있어요.
                  </p>
                </div>
                <button
                  className="btn btn--accent"
                  type="button"
                  disabled={saving}
                  onClick={() => {
                    setValues(demoPropertyTerms);
                    setOutcome(null);
                    setStatus(
                      "데모 입력 예시를 불러왔습니다. 값을 자유롭게 바꿔보세요.",
                    );
                  }}
                >
                  데모 입력 예시 불러오기
                </button>
              </div>
              <p className="demo-input-note">
                <strong>데모 입력 예시</strong>는 입력 형식을 보여주기 위한
                값이며 실매물·공식 근거가 아닙니다.
              </p>
              <form className="property-form" onSubmit={submit}>
                <label className="field">
                  <span>점포 주소</span>
                  <input
                    required
                    value={values.address}
                    onChange={(event) =>
                      setValue("address", event.target.value)
                    }
                  />
                </label>
                <label className="field">
                  <span>면적(㎡)</span>
                  <input
                    required
                    min="1"
                    step="0.1"
                    type="number"
                    value={values.area_sqm}
                    onChange={(event) =>
                      setValue("area_sqm", event.target.value)
                    }
                  />
                </label>
                <label className="field">
                  <span>보증금(만원)</span>
                  <input
                    required
                    min="0"
                    type="number"
                    value={values.deposit_manwon}
                    onChange={(event) =>
                      setValue("deposit_manwon", event.target.value)
                    }
                  />
                </label>
                <label className="field">
                  <span>월세(만원)</span>
                  <input
                    required
                    min="0"
                    type="number"
                    value={values.monthly_rent_manwon}
                    onChange={(event) =>
                      setValue("monthly_rent_manwon", event.target.value)
                    }
                  />
                </label>
                <label className="field">
                  <span>관리비(만원)</span>
                  <input
                    required
                    min="0"
                    type="number"
                    value={values.management_fee_manwon}
                    onChange={(event) =>
                      setValue("management_fee_manwon", event.target.value)
                    }
                  />
                </label>
                <label className="field">
                  <span>권리금(만원)</span>
                  <input
                    min="0"
                    type="number"
                    value={values.key_money_manwon}
                    onChange={(event) =>
                      setValue("key_money_manwon", event.target.value)
                    }
                  />
                </label>
                <div className="property-form__action">
                  <button
                    className="btn btn--primary"
                    disabled={saving || !selection.property_intake_enabled}
                    type="submit"
                  >
                    {saving ? "재계산 중" : "이 조건으로 비용 다시 계산"}
                  </button>
                  <p aria-live="polite">{status}</p>
                </div>
              </form>
              {outcome && (
                <section
                  className="property-comparison"
                  aria-labelledby="propertyComparisonTitle"
                >
                  <h3 id="propertyComparisonTitle">
                    {outcome.mode === "LIVE"
                      ? "임시값과 점포 반영값 비교"
                      : "입력값 기준 데모 계산"}
                  </h3>
                  {outcome.mode === "LIVE" ? (
                    <>
                      <div className="diff-row">
                        <span className="diff-label">초기 필요자금 기준</span>
                        <div className="diff-values">
                          <span className="diff-old">
                            {formatWon(
                              outcome.application.previous_financial_summary
                                .initial_cash.base,
                            )}
                          </span>
                          <span>→</span>
                          <strong className="diff-new">
                            {formatWon(
                              outcome.candidate.financial_summary.initial_cash
                                .base,
                            )}
                          </strong>
                        </div>
                      </div>
                      <div className="diff-row">
                        <span className="diff-label">월 고정비 기준</span>
                        <div className="diff-values">
                          <span className="diff-old">
                            {formatWon(
                              outcome.application.previous_financial_summary
                                .monthly_fixed_cost.base,
                            )}
                          </span>
                          <span>→</span>
                          <strong className="diff-new">
                            {formatWon(
                              outcome.candidate.financial_summary
                                .monthly_fixed_cost.base,
                            )}
                          </strong>
                        </div>
                      </div>
                      <p>
                        입력한 보증금·월세·관리비·권리금만 확정값으로
                        교체했습니다. 공사·장비 등 나머지는 기존 참고 범위를
                        유지합니다.
                      </p>
                    </>
                  ) : (
                    <>
                      <div className="diff-row">
                        <span className="diff-label">점포 계약 초기비용</span>
                        <strong className="diff-new">
                          {formatWon(outcome.initialPropertyCost)}
                        </strong>
                      </div>
                      <div className="diff-row">
                        <span className="diff-label">월 임차 관련 비용</span>
                        <strong className="diff-new">
                          {formatWon(outcome.monthlyOccupancyCost)}
                        </strong>
                      </div>
                      <p>
                        보증금·권리금과 월세·관리비만 합산한 데모 값입니다.
                        서버에 저장되거나 전체 창업비에 반영된 결과는 아닙니다.
                      </p>
                    </>
                  )}
                </section>
              )}
            </article>
            <DocumentIntake
              client={client}
              projectId={projectId}
              enabled={selection.document_intake_enabled}
              onApplied={onDocumentApplied}
              onViewResult={onBack}
            />
            <article
              className="surface"
              aria-labelledby="evidenceChecklistTitle"
            >
              <div className="surface__head">
                <h2 id="evidenceChecklistTitle">다음에 확인할 자료</h2>
                <p>
                  점포 비용을 반영한 뒤 견적과 계약 조건을 순서대로 확인하면
                  돼요.
                </p>
              </div>
              {selection.required_evidence.length ? (
                <ol className="preparation-checklist">
                  {selection.required_evidence.map((item, index) => (
                    <li key={item.code}>
                      <span className="preparation-checklist__number">
                        {index + 1}
                      </span>
                      <div>
                        <strong>{item.title}</strong>
                        <p>{item.reason}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>현재 별도로 지정된 필수 자료는 없어요.</p>
              )}
            </article>
          </section>
          <aside className="preparation-side">
            <PreparationProcedures
              guide={guide}
              busy={busy}
              error={error}
              onRetry={onRetry}
            />
            <article className="decision-note">
              <strong>지금 하는 일</strong>
              <span>
                계약 전 점포 조건을 넣어, 선택한 안이 예산에 가까워지는지
                확인합니다.
              </span>
            </article>
          </aside>
        </div>
      </main>
      <footer className="footer">
        <strong>CaffeMate</strong>
        <span>카페 창업, 감이 아닌 데이터로 시작하세요.</span>
      </footer>
    </div>
  );
}

