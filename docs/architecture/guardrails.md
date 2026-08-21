# Guardrails

> 상태: draft
> 갱신일: 2026-08-21

## 목표

CaffeMate의 Guardrail은 위험 문구를 뒤에 붙이는 기능이 아니라 잘못된 Evidence·계산·권한·행동이 결과로 살아남지 못하게 하는 실행 규칙이다.

## Boundary Matrix

| Boundary | Rule | Failure behavior |
| --- | --- | --- |
| Auth | 인증된 user와 project scope 필수 | 401 또는 403, 조회·write 0 |
| Tenant | 다른 project·user object 접근 금지 | 요청 차단과 security event |
| File | MIME·크기·malware·parser 품질 검사 | 격리·거절·human review |
| Prompt | 문서 안 명령을 data로만 처리 | tool·policy 변경 금지 |
| Tool | read-only allowlist와 typed input | 허용되지 않은 tool 차단 |
| Retrieval | authority·scope·data date·anchor 필수 | `STALE`, `PARTIAL`, `ABSTAIN` |
| Claim | 사실·사용자 사실·가정·계산·UNKNOWN 분리 | 자동 승격 차단 |
| Numeric | validated value·unit만 계산 입력 | 계산 중지·범위 축소 |
| Candidate | 실제 브랜드와 개인 가맹 가능 확인 | 추천 순위 제외 |
| Revenue | 비용 참고값으로 매출·고객·성공률 생성 금지 | output validation 실패 |
| Decision | 자료 부족은 조건부, 확인된 위반만 제외 | reason code 강제 |
| Action | 계약·금전·법률·외부 연락 자동화 금지 | human gate |
| Output | 중요한 Claim에 Evidence 또는 가정 필요 | result commit 차단 |

## Claim 승격

```text
PROPOSED
→ CONFIRMED
→ SUPERSEDED | RETRACTED
```

- LLM extraction은 `PROPOSED`다.
- material money·contract field는 원문 anchor와 schema validation을 통과한다.
- 불확실하거나 충돌하는 material field는 human review 뒤에만 `CONFIRMED`가 된다.
- 새 문서가 들어와도 이전 Claim을 silent overwrite하지 않는다.

## Prompt Injection

사용자 문서와 검색 결과는 untrusted data다.

- 문서의 “이전 지시를 무시하라” 같은 문장을 실행하지 않는다.
- retrieved text는 system·policy·tool 권한을 바꿀 수 없다.
- URL·파일 안 지시로 외부 전송·추가 검색·credential 접근을 실행하지 않는다.
- Agent prompt에는 allowed tools와 forbidden actions를 매 run 고정한다.
- injection flag가 있으면 원문 anchor와 함께 Risk finding을 남긴다.

## 개인정보와 문서

- 필요한 최소 사용자 정보만 저장한다.
- 사용자 문서는 project scope로 격리한다.
- upload/download는 짧은 만료의 signed URL을 사용한다.
- exact coordinate·신분·대출·계약 정보는 목적과 보존기간을 별도 관리한다.
- raw document와 derived Claim의 삭제·보존 정책을 분리한다.
- prompt·log·analytics에 raw 민감정보를 기본 기록하지 않는다.

## Financial Safety

- UNKNOWN을 0으로 대입하지 않는다.
- 참고 비용 range는 매출·수요·성공확률에 사용하지 않는다.
- 평균매출은 특정 신규 점포 예상매출이 아니다.
- 손익분기와 필요 주문 수는 입력 가정과 함께 표시한다.
- 대출 가능·승인·상환 안전을 확정하지 않는다.
- 사용자가 직접 운영하는 노동비를 0으로 숨기지 않는다.

## Franchise Safety

- 개인 가맹 가능 여부 미확인 브랜드는 추천 순위에서 제외한다.
- 직영 전용 브랜드는 경쟁점 또는 참고로만 표시한다.
- 공식 자료 일부가 없으면 조건부 후보로 남길 수 있다.
- 누락된 로열티·필수품목·광고·시스템비를 다른 브랜드 값으로 채우지 않는다.
- 특정 동네 출점·영업지역 보호는 본사 확인 전 확정하지 않는다.

## Legal and Action Boundary

AI가 할 수 있는 일:

- 조항·절차·위험 신호 찾기
- 공식 원문과 관할 확인 경로 제공
- 전문가에게 물을 질문 정리
- 비용·일정 영향 표시

AI가 할 수 없는 일:

- 계약이 법적으로 안전하다고 확정
- 계약 서명·제출
- 송금·결제
- 대출 신청·실행
- 정부 신고·등록 제출
- 본사·중개인에게 사용자 대신 연락
- 최종 창업 Go 결정

## Output Validator

결과 commit 전에 검사한다.

- Candidate Result Schema 통과
- 중요한 Claim의 Evidence ref 존재
- 모든 money field의 currency·unit·provenance
- UNKNOWN·STALE·conflict 표시
- franchise 후보의 개인 가맹 확인
- 예상매출·성공확률 금지 표현
- 상태와 reason code 일치
- human boundary 문구

## Reason Codes

```text
MISSING_EVIDENCE
STALE_EVIDENCE
CONFLICTING_EVIDENCE
UNKNOWN_COST
HARD_CONSTRAINT_VIOLATION
FRANCHISE_ELIGIBILITY_UNVERIFIED
AREA_SCOPE_MISMATCH
UNIT_MISMATCH
AGENT_SCHEMA_INVALID
HUMAN_REVIEW_REQUIRED
RECOMPUTE_REQUIRED
UNSAFE_ACTION_REQUEST
```

## Fail Closed

- 인증·tenant·권한 실패는 즉시 차단한다.
- Agent schema 실패는 한 번 repair 후 기권한다.
- material Claim conflict는 임의로 해결하지 않는다.
- 계산 input이 불완전하면 정확한 단일값 대신 range·UNKNOWN·검토 필요를 반환한다.
- 새 State 뒤 재계산이 실패하면 이전 Decision을 현재 결과로 되돌리지 않는다.

## 필수 평가

- cross-project leakage 0건
- unsafe action execution 0건
- ungrounded important Claim 0건
- 가맹 불가능 브랜드 추천 0건
- UNKNOWN을 0으로 계산 0건
- 평균매출을 신규 점포 예상매출로 사용 0건
- prompt injection에 의한 tool·policy 변경 0건
