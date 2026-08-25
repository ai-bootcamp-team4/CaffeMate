import { ControlApiError, type ResultCandidate } from "./apiClient";

const internalLabels: Record<string, string> = {
  REVIEW_RECOMMENDED: "검토 추천",
  CONDITIONAL_REVIEW: "조건부 검토",
  EXCLUDED: "현재 검토에서 제외",
  CURRENT_CONSTRAINTS_SATISFIED: "현재 입력 조건을 충족함",
  INITIAL_CASH_LOW_UNKNOWN: "최소 창업비 확인 필요",
  OWN_FUNDS_COVER_HIGH_SCENARIO: "보유 자금으로 높은 비용 범위까지 충당 가능",
  MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS: "최소 창업비가 보유 자금을 초과함",
  CAPITAL_COVERAGE_REQUIRES_CONFIRMATION: "자금 충당 가능 범위 확인 필요",
  MATERIAL_COST_UNKNOWN: "핵심 비용 확인 필요",
  MATERIAL_FIELD_MISSING: "핵심 정보 확인 필요",
  FOUNDER_FIT_HARD_CONFLICT: "운영 방식과 창업자 조건이 맞지 않음",
  FOUNDER_FIT_REQUIRES_CONFIRMATION: "운영 적합도 확인 필요",
  CRITICAL_RISK_REQUIRES_REVIEW: "중대한 위험 검토 필요",
  FRANCHISE_INELIGIBLE: "개인 가맹 대상이 아님",
  FRANCHISE_ELIGIBILITY_UNVERIFIED: "개인 가맹 자격 확인 필요",
  FRANCHISE_UNAVAILABLE_IN_AREA: "희망 지역에 출점할 수 없음",
  FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED: "희망 지역 출점 가능 여부 확인 필요",
  HQ_CONFIRMATION_REQUIRED: "본사 확인 필요",
  AVAILABLE: "가능",
  UNAVAILABLE: "불가",
  UNKNOWN: "확인되지 않음",
  VERIFIED: "확인 완료",
  UNVERIFIED: "확인 필요",
  INELIGIBLE: "대상 아님",
  RESOLVED: "지역 확인 완료",
  UNRESOLVED: "지역 확인 필요",
  N0_NATIONWIDE_FACTS: "전국 기준 자료 없음",
  NO_NATIONWIDE_FACTS: "전국 기준 자료 없음",
  N1_NATIONWIDE_CONDITIONAL: "전국 조건부 기준 자료",
  R2_REGIONAL_CONNECTOR: "지역 데이터 연결됨",
  C3_CASE_ARTIFACT: "사용자 제출 자료 반영",
  ACQUISITION_OR_PREMIUM: "권리금·영업권",
  CONSTRUCTION: "인테리어·시설 공사비",
  CONTINGENCY: "예비비",
  DEPOSIT: "임차 보증금",
  EQUIPMENT: "장비비",
  FRANCHISE_INITIAL_FEES: "가맹 초기 비용",
  MONTHLY_LABOR: "월 인건비",
  MONTHLY_OCCUPANCY: "월 임차료·관리비",
  MONTHLY_OTHER_FIXED: "기타 월 고정비",
  OPENING_INVENTORY: "오픈 초기 재고",
  OPERATING_RESERVE: "운영 예비자금",
  PREOPENING: "개업 전 준비비",
  premium: "권리금·영업권",
  royalty: "로열티",
  area_availability_hq_confirmation: "희망 지역 출점 가능 여부",
  franchise_disclosure: "정보공개서",
  franchise_disclosure_freshness: "최신 정보공개서",
  administrative_dong_mapping: "행정동 연결 정보",
  estimated_store_sales: "점포 추정 매출",
  "operations.menu_complexity": "메뉴 구성 복잡도",
  "operations.open_hours_per_day": "하루 영업시간",
  "operations.owner_hours_per_week": "창업자 주간 근무시간",
  "operations.staff_count": "직원 수",
  target_area_input: "희망 지역",
  own_funds_krw: "자기자금",
  borrowing_intent: "대출 활용 의향",
  cafe_type_preference: "창업 유형",
  operation_mode: "운영 방식",
  desired_opening_period: "희망 개업 시기",
  prior_cafe_experience: "카페 운영 경험",
  preferences: "선호 조건",
  avoidances: "제외 조건",
  initial_cash_krw: "초기 필요자금",
  INITIAL_CASH: "초기 필요자금",
  MONTHLY_FIXED_COST: "월 고정비",
  BREAK_EVEN: "손익분기 계산",
  CAPITAL: "자금 조건",
  PASSED: "통과",
  REQUIRES_HUMAN: "사람 확인 필요",
  CURRENT: "현재 기준",
  STALE: "이전 기준",
  PROCESSING: "처리 중",
  REVIEW_REQUIRED: "변경 확인 필요",
  CLARIFICATION_REQUIRED: "추가 설명 필요",
  NOOP: "변경 없음",
  UNSUPPORTED: "지원하지 않는 요청",
  EXPIRED: "확인 기한 만료",
  CONFIRMED: "반영 완료",
  CANCELLED: "취소됨",
  QUEUED: "분석 대기 중",
  RUNNING: "분석 중",
  WAITING_FOR_HUMAN: "추가 확인 필요",
  SUCCEEDED: "분석 완료",
  PARTIAL: "일부 정보로 분석 완료",
  FAILED: "분석 실패",
  AREA_RESOLUTION: "희망 지역 확인",
  CLAIM_PLAN: "확인할 정보 정리",
  EVIDENCE_PLAN: "자료 조사 계획",
  EVIDENCE_RETRIEVAL: "자료 조회",
  EVIDENCE_ASSESS: "자료 신뢰도 검토",
  EVIDENCE_FREEZE: "근거 확정",
  INDEPENDENT_SEED: "개인카페 운영안 준비",
  FRANCHISE_ELIGIBILITY: "가맹 후보 확인",
  PROPOSE_INDEPENDENT: "개인카페 제안 작성",
  PROPOSE_FRANCHISE: "프랜차이즈 제안 작성",
  CALCULATE_GATE_RANK: "비용·조건 비교",
  CANDIDATE_AUDIT: "후보 독립 검토",
  COMMIT_RESULT: "결과 확정",
};

export function internalLabel(value: string, fallback = "추가 확인 필요") {
  const known = internalLabels[value];
  if (known) return known;
  if (value.startsWith("/founder/"))
    return internalLabels[value.slice("/founder/".length)] ?? "창업 조건";
  const looksInternal =
    /^[A-Z][A-Z0-9_]{2,}$/.test(value) ||
    /^[a-z][a-z0-9]*(?:[._][a-z0-9_]+)+$/.test(value) ||
    /^(risk|candidate|proposal|evidence|assumption|brand)-[a-z0-9-]+$/i.test(
      value,
    );
  return looksInternal ? fallback : value;
}

export function displayText(value: string) {
  let next = value;
  for (const [internal, label] of Object.entries(internalLabels).sort(
    ([left], [right]) => right.length - left.length,
  )) {
    next = next.replaceAll(internal, label);
  }
  return next
    .replace(/\b[A-Z][A-Z0-9_]{2,}\b/g, "추가 확인 항목")
    .replace(/\b[a-z][a-z0-9]*(?:[._][a-z0-9_]+)+\b/g, "추가 확인 항목")
    .replace(
      /\b(?:risk|candidate|proposal|evidence|assumption|brand)-[a-z0-9-]+\b/gi,
      "추가 확인 항목",
    );
}

export function userError(error: unknown, fallback: string) {
  if (!(error instanceof Error)) return fallback;
  if (/failed to fetch|network|load failed/i.test(error.message)) {
    return "서버 연결이 잠시 끊겼어요. 같은 화면에서 다시 시도해 주세요.";
  }
  if (/candidate result is stale|must be regenerated/i.test(error.message)) {
    return "입력 조건이 바뀌었어요. 최신 조건으로 다시 계산한 뒤 선택해 주세요.";
  }
  const message = displayText(error.message);
  return !/[가-힣]/.test(message) ||
    /\b(?:candidate|result|state|workflow|contract)\b/i.test(message)
    ? fallback
    : message;
}

export function explanationError(error: unknown) {
  if (error instanceof ControlApiError) {
    if (error.status === 409) {
      return "결과가 갱신되었거나 아직 설명할 준비가 끝나지 않았어요. 최신 결과를 다시 확인한 뒤 질문해 주세요.";
    }
    if (error.status === 503) {
      return "결과 설명 기능에 잠시 연결할 수 없어요. 현재 결과는 그대로 보관되어 있으니 잠시 후 다시 질문해 주세요.";
    }
  }
  return userError(
    error,
    "답변을 만들지 못했어요. 현재 결과는 바뀌지 않았으니 질문을 조금 다르게 적어 다시 시도해 주세요.",
  );
}

export function displayValue(value: unknown): string {
  if (value == null || value === "") return "없음";
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "string") return internalLabel(value);
  if (typeof value === "number") return value.toLocaleString("ko-KR");
  if (Array.isArray(value))
    return value.map(displayValue).join(" · ") || "없음";
  return "변경됨";
}

export function uniqueLabels(values: string[], fallback?: string) {
  return [...new Set(values.map((value) => internalLabel(value, fallback)))];
}

export function formatWon(value: number | null | undefined) {
  return value == null
    ? "확인되지 않음"
    : `${new Intl.NumberFormat("ko-KR").format(value)}원`;
}

export function formatRange(
  range: ResultCandidate["financial_summary"]["initial_cash"],
) {
  if (range.low == null || range.base == null || range.high == null)
    return "확인되지 않음";
  return `${formatWon(range.low)} ~ ${formatWon(range.high)} (기준 ${formatWon(range.base)})`;
}

export function candidateSource(candidate: ResultCandidate) {
  return candidate.case_type === "INDEPENDENT"
    ? `INDEPENDENT:${candidate.independent_model?.model_id ?? ""}`
    : `FRANCHISE:${candidate.franchise?.brand_id ?? ""}`;
}

export function marketSignalLabel(
  signal: NonNullable<ResultCandidate["market_signals"]>[number],
) {
  return {
    CAFE_COUNT: "카페 업종 점포",
    OPEN_COUNT: "분기 신규 신고",
    CLOSE_COUNT: "분기 폐업 신고",
    CLOSURE_RATE: "분기 폐업 변화율",
    ESTIMATED_SALES: "분기 상권 추정매출",
    FOOT_TRAFFIC: "분기 추정 유동인구",
    RESIDENT_POPULATION: "거주인구",
    WORKER_POPULATION: "직장인구",
  }[signal.signal_type];
}

export function marketSignalValue(
  signal: NonNullable<ResultCandidate["market_signals"]>[number],
) {
  if (signal.signal_type === "ESTIMATED_SALES") return formatWon(signal.value);
  if (signal.signal_type === "CLOSURE_RATE")
    return `${signal.value.toLocaleString("ko-KR")}%`;
  if (signal.signal_type === "FOOT_TRAFFIC")
    return `${signal.value.toLocaleString("ko-KR")}명·회`;
  if (
    signal.signal_type === "RESIDENT_POPULATION" ||
    signal.signal_type === "WORKER_POPULATION"
  )
    return `${signal.value.toLocaleString("ko-KR")}명`;
  return `${signal.value.toLocaleString("ko-KR")}개`;
}

export function formatDataDate(value: string | null) {
  if (!value) return "기준일 확인 필요";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeZone: "Asia/Seoul",
  }).format(new Date(`${value}T00:00:00+09:00`));
}

export function isHttpSource(value: string) {
  return /^https?:\/\//i.test(value);
}
