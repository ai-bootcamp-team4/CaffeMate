# State와 Workflow

> 상태: draft
> 갱신일: 2026-08-21

## 원칙

- 모든 대화를 다시 해석하지 않고 새 입력을 current State의 delta로 처리한다.
- 사용자 한 명은 여러 창업 검토 프로젝트를 가질 수 있다.
- 프로젝트 사이의 State·Evidence·문서·계산은 섞이지 않는다.
- State version마다 그 판단에 사용한 Evidence와 Policy snapshot을 고정한다.
- Agent와 MCP는 State를 직접 쓰지 않는다.
- persistent write는 reducer 하나만 수행한다.

## Aggregate 경계

```text
Account
└── Venture Project
    ├── Founder State Versions
    ├── Area Profile Snapshots
    ├── Venture Cases
    ├── Evidence and Claims
    ├── Assumptions and Conflicts
    ├── Calculation Snapshots
    ├── Decision Snapshots
    ├── Documents
    └── Workflow Runs
```

## 주요 State

### Account State

```yaml
authenticated_user_id: required
active_project_id: optional
venture_project_ids: []
```

### Founder State

```yaml
target_area: required
funding:
  own_funds_krw: required
  borrowing_intent: YES | NO | UNDECIDED
cafe_type_preference: OPEN_TO_BOTH | INDEPENDENT_ONLY | FRANCHISE_ONLY
operation_mode: DIRECT_FULL_TIME | DIRECT_PART_TIME | EMPLOYEE_LED | UNDECIDED
optional_preferences: []
```

### Area State

```yaml
area_identity: required
coverage_profile_id: required
population_evidence: []
age_evidence: []
cafe_supply_evidence: []
opening_closure_evidence: []
sales_evidence: []
unavailable_fields: []
```

### Venture Case

```yaml
case_id: required
case_type: INDEPENDENT | FRANCHISE
maturity: CONCEPT | CANDIDATE | PROPERTY_LINKED | DOCUMENT_LINKED
status: DRAFT | CONDITIONALLY_REVIEWABLE | EXCLUDED | SELECTED
confirmed_claim_ids: []
assumption_ids: []
missing_fields: []
```

기계 검증 구조는 [Venture State Schema](../contracts/venture-state.schema.json)를 따른다.

## Event

State 변경은 command가 아니라 검증된 Event로 기록한다.

```yaml
event_id: required
project_id: required
actor_id: required
event_type: required
base_state_version: required
idempotency_key: required
payload: {}
occurred_at: required
```

주요 event type:

- `PROJECT_CREATED`
- `ONBOARDING_CONFIRMED`
- `FEEDBACK_CHANGE_PROPOSED`
- `FEEDBACK_CHANGE_CONFIRMED`
- `CANDIDATE_SELECTED`
- `DOCUMENT_UPLOADED`
- `CLAIM_CONFIRMED`
- `CLAIM_RETRACTED`
- `EVIDENCE_STALE`
- `RECOMPUTE_REQUESTED`

## State write contract

### Phase A — 입력 반영

한 transaction에서 다음을 기록한다.

- Event
- 새 State version
- Evidence·Policy input snapshot
- 영향받은 이전 Calculation·Decision의 current pointer 무효화
- active processing state

결정값이 사용자 확인을 요구하면 Phase B를 시작하지 않고 review task를 남긴다.

### Phase B — 파생 결과

같은 State version에 대해 다음을 한 transaction에서 기록한다.

- Calculation snapshot
- Risk findings
- Candidate result
- Decision snapshot
- active result pointer

Workflow가 실패하면 Phase A의 새 State는 남기되 이전 Decision을 현재 결과로 되돌리지 않는다.

## Workflow 종류

### `FIRST_PROPOSAL`

```text
onboarding confirmed
→ area identity·coverage
→ required Claim plan
→ Evidence retrieval
→ independent·franchise candidate branches
→ deterministic calculation·Gate
→ independent Critic
→ commit result
```

종료:

- 유효 후보가 하나 이상이면 결과 생성
- 후보가 없으면 없는 이유와 다음 입력 요청
- 자료 부족은 후보가 가능한 경우 `조건부 검토`

### `RESULT_FEEDBACK`

```text
result exists
→ natural language delta proposal
→ affected fields and candidates
→ before/after preview
→ user confirm or cancel
→ new State version
→ selective rerun
```

확인 전 persistent State 변경은 0건이어야 한다.

### `CANDIDATE_SELECTION`

```text
candidate selected
→ case maturity update
→ missing evidence checklist
→ property·document intake open
```

선택은 계약 또는 최종 창업 결정을 의미하지 않는다.

### `DOCUMENT_UPDATE`

```text
document uploaded
→ file validation
→ parsing·OCR·table recovery
→ proposed Claims with anchors
→ user review when material
→ conflict detection
→ selective recalculation
→ decision delta
```

### `EVIDENCE_REFRESH`

```text
new source snapshot
→ affected Evidence identified
→ freshness and conflict update
→ dependent result invalidation
→ background recompute or review request
```

## Workflow run 상태

```text
QUEUED
→ RUNNING
→ WAITING_FOR_HUMAN | SUCCEEDED | PARTIAL | FAILED
```

- `PARTIAL`은 완성된 결과를 의미하지 않는다.
- `WAITING_FOR_HUMAN`은 필요한 질문과 대상 field를 포함해야 한다.
- timeout·retry 횟수와 tool trace를 저장한다.
- 실패한 Agent output은 State input으로 승격하지 않는다.

## 동시성

- command는 `base_state_version`을 요구한다.
- current version과 다르면 conflict를 반환하고 자동 덮어쓰지 않는다.
- 같은 idempotency key와 payload 재시도는 같은 Event·결과를 반환한다.
- 이전 State version을 사용한 늦은 Agent 결과는 폐기한다.

## 수용 기준

- 프로젝트 전환으로 값이 섞이지 않는다.
- 결과 피드백 확인 전 State가 바뀌지 않는다.
- 동일 input snapshot은 동일한 계산 결과를 만든다.
- 문서 변경은 의존하는 계산과 판단만 무효화한다.
- 이전 State와 Decision은 감사 이력으로 남는다.
- Agent 실패 후에도 partial candidate가 current 결과가 되지 않는다.
