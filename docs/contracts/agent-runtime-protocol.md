# CaffeMate 백엔드·Agent Runtime 연결 계약

> 상태: active implementation contract
>
> 계약 버전: `1.0.0`
>
> 제품 정본: [제품 명세](../product-spec.md)
>
> 기술 기준: [Agent·RAG 런타임 상세 계약](../product-very-spec.md)
>
> 갱신일: 2026-08-21

## 1. 목적과 권한

이 문서는 다음 세 경계의 구현 계약이다.

1. Control API가 GCP Agent Runtime을 호출하는 방법
2. Control API가 Agent 결과를 검증하고 Workflow에 반영하는 방법
3. Control API가 private MCP에서 근거 자료를 읽는 방법

제품 행동은 `product-spec.md`가 우선한다. 이 문서는 제품 결정을 바꾸지 않고 서비스 사이의 전송 형식, 권한, 실패와 재시도 규칙을 고정한다. JSON 구조는 아래 기계 계약이 권위값이다.

- [Agent Task Schema](./agent-task.schema.json)
- [Agent Task Result Schema](./agent-task-result.schema.json)
- [Agent Role Payload Schema](./agent-role-payloads.schema.json)
- [공통 Typed Value Schema](./common-types.schema.json)
- [MCP Tool Contract Schema](./mcp-tool-contracts.schema.json)
- [MCP Tool Manifest](./mcp-tool-manifest.json)
- [Venture State Schema](./venture-state.schema.json)
- [Evidence Record Schema](./evidence-record.schema.json)
- [Candidate Result Schema](./candidate-result.schema.json)
- [Document Extraction Form Schema](./document-extraction-form.schema.json)

## 2. 소유권과 변경 승인

| 경계 | Producer | Consumer·권위자 | 변경 승인 |
| --- | --- | --- | --- |
| 공개 API 응답 | Control API | React Web | 김민석 + 유시우 |
| `AgentTask` | Control API | Agent Runtime adapter | 김민석 + 이민우 |
| `AgentTaskResult` | Agent Runtime | Control API validator | 이민우 + 김민석 |
| MCP tool request | Control API | MCP server | 김민석 + 이민우 |
| MCP `structuredContent` | MCP server | Control API | 이민우 + 김민석 |
| State·Calculation·Gate·rank | Control API deterministic core | React Web·Agent 입력 snapshot | 김민석 |
| Prompt·role payload | Agent Runtime | Control API semantic validator | 이민우 + 김민석 |

Producer는 Schema, 정상 fixture, 실패 fixture와 consumer 영향 변경을 같은 풀 리퀘스트에 포함한다. Consumer owner는 문서만 읽고 승인하지 않고 실제 parser·generated type 또는 contract test와 대조한다.

## 3. 고정 연결 구조

```mermaid
flowchart LR
    web[React Web] -->|public JSON API| api[Control API]
    api -->|GCP authenticated run| runtime[Agent Runtime ADK App]
    api -->|MCP 2026-07-28 tools/call| mcp[Private MCP]
    runtime -->|AgentTaskResult only| api
    mcp -->|structuredContent only| api
    api --> core[Deterministic Finance Gate Rank]
    core --> reducer[Single State Reducer]
```

### 3.1 확정 경계

- React Web은 Agent Runtime과 MCP를 직접 호출하지 않는다.
- 다섯 Agent는 하나의 ADK application에 배포한다.
- Agent 간 자유 대화, Agent 간 네트워크 호출과 A2A는 사용하지 않는다.
- Control API가 고정 DAG, 실행 Agent, 입력 snapshot, 재시도와 종료를 결정한다.
- Agent는 typed proposal만 반환한다. State·Evidence·Calculation·Gate·rank를 쓰지 않는다.
- MCP는 read-only data plane이다. 추천·계산·write tool을 제공하지 않는다.
- 첫 구현에서 Agent Runtime은 MCP를 직접 호출하지 않는다.
- Evidence Researcher `PLAN`은 필요한 read action만 제안한다. Control API가 allowlist와 인자를 검증해 MCP를 실행하고 결과를 `ASSESS` 입력으로 전달한다.

이 경계로 Agent의 잘못된 tool 선택이 권한 실행으로 바로 이어지는 것을 막고, MCP 호출을 같은 Workflow trace와 project fence 안에서 재현할 수 있게 한다.

## 4. Agent Runtime 물리 전송

### 4.1 현재 배포 가능 상태

`asia-northeast3` Agent Runtime 배치는 확정이다. 그러나 2026-08-21 공식 모델 문서상 기존 선택인 `gemini-3.5-flash`의 지원 생성 리전에 서울이 없다. 따라서 생성 모델 id는 현재 `PENDING_HUMAN_DECISION`, Agent 경로는 `BLOCKED_BY_REGION`이다.

- 서울 Runtime을 다른 리전으로 자동 변경하지 않는다.
- `global` model endpoint로 자동 fallback하지 않는다.
- Runtime 생성, 생성 모델 호출, embedding 호출, reranker 호출을 서로 독립된 배포 preflight로 실행한다.
- 네 항목 중 필수 항목 하나라도 서울에서 실패하면 Agent Workflow를 시작하지 않고 global 호출 수가 0임을 검증한다.
- 서울에서 실제 `generateContent` read-back을 통과한 모델 id를 인간이 승인하고 release manifest에 pin한 뒤에만 차단을 해제한다.

### 4.2 GCP endpoint와 관리형 session

Control API의 물리 설정은 `gcp_project_id`와 `resource_id`를 분리한다. 제품의 `venture_project_id`를 GCP endpoint 조립에 사용하지 않는다.

한 invocation은 다음 순서만 사용한다.

1. `POST .../reasoningEngines/{resource_id}:query`의 `async_create_session`으로 ADK 관리형 session을 생성한다.
2. 반환된 opaque session id를 아래 `:streamQuery`의 `session_id`로 전달한다.
3. terminal outcome 뒤 `async_delete_session`을 같은 `:query` endpoint로 호출한다.
4. 삭제 실패는 durable cleanup outbox로 넘겨 재시도하고, 재시도 예산 소진 시 운영 경고를 만든다.

```text
POST https://asia-northeast3-aiplatform.googleapis.com/v1/projects/{gcp_project_id}/locations/asia-northeast3/reasoningEngines/{resource_id}:query
class_method=async_create_session

POST https://asia-northeast3-aiplatform.googleapis.com/v1/projects/{gcp_project_id}/locations/asia-northeast3/reasoningEngines/{resource_id}:streamQuery?alt=sse
class_method=async_stream_query

POST https://asia-northeast3-aiplatform.googleapis.com/v1/projects/{gcp_project_id}/locations/asia-northeast3/reasoningEngines/{resource_id}:query
class_method=async_delete_session
```

session 생성 입력의 `user_id`는 실제 사용자 식별자가 아니라 `venture_project_id`를 서버 비밀값으로 HMAC한 `p-<digest>`다. Control API가 session id를 추측하거나 직접 만들지 않는다. 별도 Sessions REST API와 `async_create_session`을 혼용하지 않으며 session TTL을 제품 계약으로 주장하지 않는다. 모든 `AgentTask`는 session 이력 없이 완전해야 하고 session은 제품 State나 대화 기억이 아니다.

`:streamQuery` body는 다음과 같다.

```json
{
  "class_method": "async_stream_query",
  "input": {
    "user_id": "p-<venture-project-hmac>",
    "session_id": "<async_create_session returned id>",
    "message": "<RFC 8785 canonical AgentTask JSON>"
  }
}
```

### 4.3 결정론적 root dispatcher

ADK application의 root는 LLM Agent가 아니라 custom `BaseAgent`인 `CAFFEMATE_TASK_DISPATCHER`다. 다음 매핑을 코드 상수와 release manifest로 함께 고정한다.

```text
INTENT_DELTA                         → INTENT_INTERPRETER
EVIDENCE_PLAN | EVIDENCE_ASSESS     → EVIDENCE_RESEARCHER
PROPOSE_INDEPENDENT | PROPOSE_FRANCHISE → PROPOSAL_AGENT
DOCUMENT_EXTRACT                    → DOCUMENT_ANALYST
CANDIDATE_AUDIT                     → TYPED_CANDIDATE_AUDITOR
```

dispatcher는 모델을 호출하기 전에 `AgentTask` Schema, `task_type → agent_name → prompt_version → input/output schema` 조합과 `NO_DIRECT_TOOL_CALLS`를 검증한다. 그 뒤 정확히 한 child Agent의 `run_async`만 실행한다. LLM이 역할을 고르거나 다른 Agent로 transfer하지 않으며 root 자체도 생성 모델을 호출하지 않는다.

Control API가 수용하는 final event는 다음 조건을 모두 만족해야 한다.

- ADK의 final-response 판정이 참이다.
- `author`가 dispatcher가 고른 정확한 `agent_name`이다.
- partial event가 아니다.
- `content.parts`가 정확히 한 개이고 non-empty text만 가진다.
- function call, function response, binary part와 Markdown fence가 없다.
- 한 invocation에서 위 조건을 만족하는 event가 정확히 하나다.

없거나 둘 이상이면 `RUNTIME_PROTOCOL_INVALID`다. 다른 author의 final, 중간 event와 trace text는 결과 후보가 아니다.

### 4.4 인증과 전송 adapter

- Control API 전용 service account가 OAuth access token으로 세 endpoint를 호출한다.
- 이 경로는 session 생성·삭제를 `:query`, 실행을 `:streamQuery` adapter로 수행하고 배포 service account에는 대상 runtime query에 필요한 최소 권한만 부여한다.
- 사용자 token, MCP credential, database credential과 raw secret을 Agent 입력에 넣지 않는다.
- runtime의 Agent identity에는 모델 호출과 자체 session 외에 MCP, Cloud SQL, BigQuery, Cloud Storage, Secret Manager 권한을 주지 않는다.

전송 adapter는 `AgentTask` 검증·digest 재계산, session 수명주기, IAM 호출, final event 선택, JSON parsing, `AgentTaskResult`와 의미 규칙 검증을 담당한다. session event 전문은 일반 log에 남기지 않고 trace id·latency·status·digest만 남긴다.

## 5. 논리 요청 계약

기계 구조는 `agent-task.schema.json`을 따른다. 핵심 필드는 다음과 같다.

```yaml
schema_version: "1.0.0"
task_id: logical stage task id
invocation_id: physical call id
agent_name: one of five roles
task_type: registered task
workflow_run_id: required
stage_run_id: required
transport_attempt: 1..3
repair_attempt: 0..1
venture_project_id: required
head_fence: full immutable input fence
prompt_version: required
input_schema_id: required
output_schema_id: required
input_artifacts: []
input_digest: sha256 digest
deadline_at: UTC date-time
runtime_tool_policy: NO_DIRECT_TOOL_CALLS
tool_manifest_digest: Evidence plan only; otherwise null
payload: typed role input
trace_context: optional W3C trace context
```

### 5.1 Task registry

| `task_type` | `agent_name` | 입력 payload | 출력 payload | 최대 실행시간 |
| --- | --- | --- | --- | ---: |
| `INTENT_DELTA` | `INTENT_INTERPRETER` | current State projection, latest input, field ontology | delta proposal | 15초 |
| `EVIDENCE_PLAN` | `EVIDENCE_RESEARCHER` | atomic Claims, pinned MCP catalog | support·counter action plan | 20초 |
| `EVIDENCE_ASSESS` | `EVIDENCE_RESEARCHER` | Claims, validated MCP·RAG results | Evidence assessments·conflicts | 30초 |
| `PROPOSE_INDEPENDENT` | `PROPOSAL_AGENT` | Founder·Area snapshot, registered model seeds, Evidence | independent candidate proposals | 30초 |
| `PROPOSE_FRANCHISE` | `PROPOSAL_AGENT` | Founder·Area snapshot, verified franchise universe, Evidence | franchise candidate proposals | 30초 |
| `DOCUMENT_EXTRACT` | `DOCUMENT_ANALYST` | extraction contract, parser blocks, anchors | proposed Claims·risk flags | batch당 60초 |
| `CANDIDATE_AUDIT` | `TYPED_CANDIDATE_AUDITOR` | frozen candidate·Evidence·calculation·Gate | audit findings | 20초 |

각 row의 `input_schema_id`와 `output_schema_id`는 배포 manifest에 고정한다. Agent code를 구현하기 전에 역할별 payload Schema와 최소 정상·기권 fixture가 존재해야 한다. 이름만 맞고 payload Schema가 없는 Agent는 배포할 수 없다.

### 5.2 입력 투영 원칙

- Agent에는 전체 database row나 사용자 계정을 전달하지 않고 해당 역할에 필요한 projection만 보낸다.
- `project_id`와 fence는 권한을 부여하는 credential이 아니라 stale·cross-project 결과를 검출하는 식별자다.
- `input_artifacts`는 id·kind·version·digest를 기록한다. Agent가 id만으로 저장소를 다시 읽지는 않는다.
- 실제 판단에 필요한 내용은 검증된 `payload`에 inline으로 전달한다.
- Proposal 입력은 frozen Evidence Snapshot과 등록 seed·brand universe만 포함한다.
- Evidence Assessment 입력은 Control API가 실행하고 검증한 tool result만 포함한다.
- Document 입력은 한 revision의 허용 parser block·anchor만 포함한다.

### 5.3 `input_digest` 계산

`input_digest`는 같은 논리 입력을 재시도에서 식별하기 위한 값이다. 언어별 임의 JSON 직렬화가 아니라 [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)으로 canonicalize한 UTF-8 bytes에 SHA-256을 계산한다. backend와 Agent adapter는 같은 공유 test vector를 사용한다.

```text
schema_version
task_id
agent_name
task_type
workflow_run_id
stage_run_id
venture_project_id
head_fence
prompt_version
input_schema_id
output_schema_id
input_artifacts
runtime_tool_policy
tool_manifest_digest
available_tool_catalog
payload
```

`invocation_id`, `repair_of_invocation_id`, `repair_context`, `transport_attempt`, `repair_attempt`, `deadline_at`, `trace_context`와 `input_digest` 자체는 digest 대상에서 제외한다. 따라서 동일 논리 입력의 network retry와 repair는 같은 digest를 유지하고, full head·prompt·Schema·tool manifest·payload 중 하나라도 바뀌면 digest가 달라진다.

## 6. 논리 결과 계약

기계 구조는 `agent-task-result.schema.json`을 따른다.

```yaml
schema_version: "1.0.0"
task_id: echoed
invocation_id: echoed
agent_name: echoed
task_type: echoed
workflow_run_id: echoed
stage_run_id: echoed
venture_project_id: echoed
head_fence_seen: echoed full head
input_digest: echoed
output_schema_id: echoed
status: COMPLETE | NEEDS_EVIDENCE | NEEDS_HUMAN | ABSTAIN | INVALID
payload: object or null
evidence_refs: []
missing_claim_ids: []
reason_codes: []
warnings: []
```

### 6.1 Status 의미

| Status | 의미 | backend 행동 |
| --- | --- | --- |
| `COMPLETE` | 역할 payload를 완성함 | 전체 검증 뒤 다음 stage로 전달 |
| `NEEDS_EVIDENCE` | 지정 Claim의 근거가 부족함 | 필요한 근거 action 또는 조건부 결과로 전환 |
| `NEEDS_HUMAN` | 자동 해소하면 안 되는 모호성·충돌 | Workflow를 `WAITING_FOR_HUMAN`으로 전환 |
| `ABSTAIN` | 현재 입력으로 역할을 안전하게 수행할 수 없음 | 후보 수를 채우지 않고 기권 이유 기록 |
| `INVALID` | 입력 자체가 역할 계약을 위반함 | stage 실패, 원 입력 producer에 오류 반환 |

`FAILED`, `TIMEOUT`, `SAFETY_BLOCKED`, `LATE_DISCARDED`는 Agent가 주장하는 status가 아니다. Control API transport adapter가 관측한 invocation outcome으로 따로 기록한다.

### 6.2 Backend 수용 순서

결과는 다음 검사를 모두 통과해야 다음 stage 입력이 된다.

```text
GCP transport success
→ final event exactly one
→ JSON parse
→ AgentTaskResult Schema
→ task·invocation·agent·type·venture project·full head·digest echo
→ registered role payload Schema
→ Evidence and artifact reference subset
→ semantic support validator
→ product Guardrail
→ downstream deterministic calculation
```

- echo 또는 full head mismatch는 repair하지 않고 `FENCE_MISMATCH`로 폐기한다.
- Agent가 입력에 없던 Evidence id·brand id·document anchor를 반환하면 `UNSUPPORTED_REFERENCE`로 폐기한다.
- `COMPLETE`인데 required payload가 비었으면 Schema 실패다.
- 자유 문장, Markdown code fence, JSON 앞뒤 prose와 추가 top-level field는 허용하지 않는다.
- Agent 결과는 이 검사를 통과해도 persistent State가 아니다. reducer transaction 전까지 proposal이다.

### 6.3 실행 가능한 semantic invariant

JSON Schema가 두 필드 사이의 동일성, 배열 참조의 포함관계와 숫자 대소관계를 모두 표현한다고 가정하지 않는다. Control API의 단일 `validateAgentBoundary(task, result, currentHead)`가 아래 오류 코드를 반환하며 Agent adapter와 fixture도 같은 validator package를 사용한다.

| Code | 결정론적 검사 | 실패 행동 |
| --- | --- | --- |
| `FENCE_ECHO_MISMATCH` | `result.head_fence_seen`이 `task.head_fence`와 byte-equivalent인가 | 결과 폐기, repair 0 |
| `CURRENT_HEAD_MISMATCH` | 수용 직전 State·Founder·Area·Evidence·Policy·index·seed·Workflow generation이 요청 full head와 모두 같은가 | `STALE_DISCARDED`, write 0 |
| `UNALLOCATED_OUTPUT_ID` | `op_id`, `action_id`, `proposal_id`, `claim_id`가 각 입력의 backend 발급 pool·seed·universe에 있는가 | 결과 폐기 |
| `UNSUPPORTED_REFERENCE` | claim·Evidence·candidate·anchor ref가 frozen input artifact의 부분집합인가 | 결과 폐기 |
| `ASSUMPTION_USED_AS_EVIDENCE` | `DECLARED_ASSUMPTION` 또는 `UNKNOWN` id가 Evidence coverage로 쓰이지 않았는가 | 결과 폐기 |
| `EVIDENCE_SCOPE_OR_DATE_INVALID` | Evidence scope·source date·freshness가 Claim 요구와 맞는가 | 해당 Claim 미확인 처리 |
| `MCP_TOOL_CONTRACT_MISMATCH` | action의 name·version·args와 result Schema가 manifest의 같은 tool row와 맞는가 | tool 실행 또는 결과 수용 거절 |
| `MCP_PROJECT_SCOPE_MISMATCH` | MCP result의 `project_id`가 서명된 scope token과 같은가 | 403, result 0 |
| `FRANCHISE_ELIGIBILITY_UNVERIFIED` | 순위가 있는 프랜차이즈의 개인 가맹 가능 여부가 근거와 함께 `VERIFIED`인가 | `EXCLUDED`, rank null |
| `MONEY_RANGE_NON_MONOTONIC` | 알려진 비용 범위가 `low <= base <= high`인가 | 후보 계산 실패 |
| `MATERIAL_PROVENANCE_MISSING` | 추천 후보의 초기비용·고정비와 계산 입력에 material provenance가 있는가 | 최대 `CONDITIONAL_REVIEW` |
| `RANK_INVARIANT_VIOLATION` | hard Gate 통과 집합과 조건부 검토 집합이 섞이지 않고 rank가 연속·유일한가 | 전체 rank 재계산 |

`UNALLOCATED_OUTPUT_ID`를 피하기 위해 backend가 Intent의 `operation_id_pool`, Evidence plan의 `action_id_pool`, Proposal seed·brand의 `proposal_id`, 문서 추출의 `claim_id_pool`을 입력에 미리 넣는다. Agent는 새 domain id를 만들지 않는다. transport의 `invocation_id`만 adapter가 물리 호출마다 발급한다.

## 7. 중복, 재시도, 시간 초과와 취소

### 7.1 식별자

- `task_id`: 같은 Workflow generation 안의 논리 stage에 고정
- `invocation_id`: 실제 GCP 호출마다 새 값
- `transport_attempt`: 같은 logical input의 initial call과 408·429·5xx·network retry를 `1..3`으로 구분
- `repair_attempt`: 원 호출은 `0`, schema repair 호출만 `1`
- `input_digest`: payload, fence, schema와 prompt version을 포함한 canonical digest

Agent 호출은 side effect가 없으므로 동일 `task_id`가 둘 이상 실행돼도 State를 바꾸지 않는다. Worker는 `(task_id, input_digest)` unique key와 stage compare-and-swap으로 full head가 현재와 일치하는 첫 valid result만 checkpoint하고 나머지는 `DUPLICATE_DISCARDED`로 기록한다.

### 7.2 재시도 표

| 실패 | 재시도 | 처리 |
| --- | ---: | --- |
| network, HTTP 408·429·5xx | 최대 2회 | 새 `invocation_id`, 같은 `task_id`·digest, `transport_attempt` 증가 |
| JSON parse·Schema 실패 | repair 1회 | 새 invocation에 이전 response text·digest와 제한된 validator error를 `repair_context`로 전달 |
| safety block | 0회 | `SAFETY_BLOCKED` |
| HTTP 400·401·403 | 0회 | 설정·권한 오류로 실패 |
| fence·ACL·unsupported ref | 0회 | 즉시 폐기 |
| deadline 초과 | 0회 | `TIMED_OUT`, session stream 종료, 늦은 결과 폐기 |

transport backoff는 250ms, 750ms이고 invocation id에서 파생한 0~100ms jitter를 더한다. 429의 `Retry-After`는 2초 이하이면서 남은 deadline 안에 있을 때만 우선한다. session 생성, run, response validation, repair와 cleanup enqueue까지 모두 `deadline_at` 예산에 포함하며 각 호출 직전에 남은 시간이 2초 미만이면 재시도하지 않는다.

repair는 같은 session 이력에 의존하지 않는다. `repair_context`에 직전 응답 text, 그 SHA-256 digest와 최대 50개 validator error가 반드시 들어간다. 두 번째 schema 실패는 기권으로 끝나며 세 번째 생성 호출은 없다.

Workflow 취소는 GCP 계산이 즉시 중단됐음을 보장하지 않는다. Control API가 run을 `CANCELLED`로 닫고 SSE를 사용 중이면 stream을 닫는다. timeout 또는 cancel 뒤 도착한 결과는 full head가 같아도 무조건 폐기한다. 그 외 결과도 current full head의 여덟 차원이 모두 요청과 같을 때만 checkpoint한다.

### 7.3 `202` 이후 durable 실행

공개 `POST /v1/projects/{venture_project_id}/workflows/{workflow_code}`는 다음 transaction이 commit된 뒤에만 `202 + workflow_run_id`를 반환한다.

1. `workflow_run`, 첫 `stage_run`, command payload digest와 idempotency record를 Cloud SQL에 기록한다.
2. 같은 transaction의 outbox row에 `workflow_run_id`를 기록한다.
3. outbox publisher가 Pub/Sub에 발행하고 발행 완료를 기록한다.

`caffemate-worker`가 모든 Agent DAG stage의 유일한 lease owner다. Worker는 15초 heartbeat, 45초 lease를 사용하고 stage 시작·checkpoint·다음 outbox 생성에 compare-and-swap을 적용한다. API instance가 `202` 직후 종료돼도 DB outbox가 남으므로 실행이 사라지지 않는다. redelivery는 같은 `(workflow_run_id, stage_run_id, input_digest)`로 흡수한다.

Worker는 Agent Runtime·MCP credential을 갖지 않는다. Agent·MCP stage에서는 lease token과 full head를 넣어 private Control API stage-execute endpoint를 호출하고, Control API가 외부 호출과 boundary validation을 수행한 뒤 같은 lease token으로 checkpoint한다. 문서 parsing·embedding처럼 Worker가 직접 수행하는 stage도 persistent Venture State는 쓰지 않고 proposed artifact만 만들며 reducer 적용은 API를 통한다.

```text
POST   /v1/projects/{venture_project_id}/workflows/{workflow_code}
GET    /v1/projects/{venture_project_id}/workflows/{workflow_run_id}
GET    /v1/projects/{venture_project_id}/workflows/{workflow_run_id}/events
POST   /v1/projects/{venture_project_id}/workflows/{workflow_run_id}:cancel
POST   /internal/v1/workflows/{workflow_run_id}/stages/{stage_run_id}:execute
```

`/internal/**`은 제품·브라우저 API가 아니라 Worker service identity만 호출하는 배포 내부 endpoint다. 이 경로를 외부 ingress에 노출하지 않는다.

cancel command도 durable Event로 기록하고 generation을 증가시킨다. 진행 중인 worker는 다음 heartbeat 또는 외부 호출 반환 시 이를 관측하고 checkpoint를 금지한다. cleanup outbox는 cancel과 별개로 session 삭제를 끝까지 시도한다.

## 8. MCP 연결 계약

### 8.1 Protocol과 transport

- protocol revision: `2026-07-28`
- endpoint: private Cloud Run의 `POST /mcp`
- transport: stateless Streamable HTTP
- encoding: UTF-8 JSON-RPC 2.0
- 사용 기능: `server/discover`, `tools/list`, `tools/call`
- 사용하지 않는 기능: write tool, prompts, sampling, elicitation, persistent MCP session, Tasks extension
- implementation: MCP server는 공식 TypeScript SDK v2의 `createMcpHandler(..., { legacy: 'reject' })`, FastAPI Control API는 공식 Python SDK v2 client를 사용한다. 양쪽 package version을 lockfile·release manifest에 pin하며 hand-written transport와 2025 fallback은 허용하지 않는다.

모든 POST는 `MCP-Protocol-Version`과 body의 실제 method를 반영한 `Mcp-Method`를 포함한다. `Mcp-Name`은 `tools/call`처럼 `params.name`이 정의된 요청에만 포함한다. header와 body가 다르면 HTTP 400, JSON-RPC `-32020`으로 거절한다.

```text
Authorization: Bearer <Cloud Run ID token>
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: <server/discover | tools/list | tools/call>
Mcp-Name: <tool_name; tools/call only>
X-CaffeMate-Scope-Token: <short-lived signed scope>
traceparent: <W3C trace context>
```

MCP 2026-07-28은 stateless이므로 `initialize`, `notifications/initialized`와 `Mcp-Session-Id`를 사용하지 않는다. 모든 request의 `_meta`에 protocol version, client info와 client capabilities를 넣는다. 배포 preflight에서 client·server SDK가 이 revision을 실제로 교환하고 JSON과 SSE 응답을 모두 처리하는지 확인한다.

### 8.2 인증과 project scope

- Control API service identity만 MCP의 `roles/run.invoker`를 가진다.
- Cloud Run ID token의 audience는 MCP service URL 또는 설정된 custom audience와 정확히 일치해야 한다.
- `X-CaffeMate-Scope-Token`은 Control API가 발급하며 `iss`, `aud`, `venture_project_id`, `workflow_run_id`, full head digest, `jti`, `exp`를 포함한다.
- scope token 수명은 최대 5분이다.
- MCP는 Cloud Run identity와 scope token을 모두 검증한 뒤 project tool을 실행한다.
- Agent가 생성한 `venture_project_id`, scope token과 query filter는 받지 않는다.
- public corpus tool에도 trace와 source scope를 남기되 사용자 project 문서를 섞지 않는다.

### 8.3 Tool 호출

```json
{
  "jsonrpc": "2.0",
  "id": "mcp-req-123",
  "method": "tools/call",
  "params": {
    "name": "get_area_profile",
    "arguments": {
      "administrative_code": "41117590",
      "boundary_version": "2026-07-01",
      "as_of": "2026-08-21"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "caffemate-control-api",
        "version": "1.0.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

MCP 결과의 `structuredContent`만 기계 입력으로 사용하며 tool의 `outputSchema`로 양쪽에서 검증한다. 호환용 `content` text는 화면·Evidence·계산 입력으로 사용하지 않는다.

공통 `structuredContent` envelope:

```yaml
schema_version: required
request_id: required
tool_name: required
tool_version: required
status: OK | PARTIAL | STALE | NOT_FOUND | ERROR
project_id: required; signed scope의 venture project
evidence_records: []
missing_fields: []
conflicts: []
source_trace: []
observed_at: required
```

- `isError: true`는 성공 응답이 아니다.
- `PARTIAL`·`STALE`은 HTTP 성공과 별개인 domain status다.
- `NOT_FOUND`를 빈 정상 Evidence로 바꾸지 않는다.
- public tool을 포함한 모든 tool 결과의 `project_id`가 scope token의 `venture_project_id`와 다르면 전체 결과를 폐기한다.
- MCP transport 재시도는 Control API가 담당하며 tool은 같은 `request_id`에서 외부 write를 수행하지 않는다.
- JSON 응답과 request-scoped SSE의 최종 JSON-RPC response를 모두 지원한다. timeout·cancel이면 SSE stream을 닫고 이후 message를 적용하지 않는다.

### 8.4 고정 read tool registry

```text
resolve_area
get_area_profile
search_cafe_observations
search_business_events
list_franchise_universe
get_franchise_disclosure
retrieve_official_documents
retrieve_project_documents
get_official_procedure
get_source_health
```

tool 이름, input·output Schema와 version은 [MCP Tool Manifest](./mcp-tool-manifest.json)에 pin한다. 각 tool의 input·output 구조는 [MCP Tool Contract Schema](./mcp-tool-contracts.schema.json)가 권위값이다. server는 tool version을 `_meta["com.caffemate/toolVersion"]`에 반환한다.

`retrieve_official_documents`와 `retrieve_project_documents`의 production retrieval backend는 Vertex AI RAG Engine이다. Agent는 corpus resource name이나 metadata filter를 직접 전달하지 않는다. MCP server가 scope token의 `venture_project_id`, Cloud SQL의 허용 corpus·file mapping과 tool input의 document revision allowlist로 실제 검색 범위를 만든다. 공식 corpus와 project corpus를 한 요청에서 섞지 않으며, project mapping이 없거나 다르면 RAG 호출 전에 403으로 끝낸다.

인구·업소·개폐업·프랜차이즈 구조화 필드는 나머지 typed connector tool로 조회하며 문서 RAG context를 정형 수치의 최종값으로 사용하지 않는다. RAG retrieval hit도 바로 Evidence가 아니며 tool output Schema, 원문 anchor·source revision·scope·freshness 검사를 통과한 뒤에만 `evidence_records`에 들어간다.

배포 preflight는 pagination을 끝까지 소비한 `tools/list`의 name·version·inputSchema·outputSchema를 RFC 8785로 정규화한다. checked-in manifest도 같은 방식으로 정규화하고 [manifest digest](./mcp-tool-manifest.sha256)와 비교한다. 누락·추가 tool, schema 차이 또는 digest 차이가 하나라도 있으면 `MCP_MANIFEST_MISMATCH`로 Workflow 시작을 막는다. `server/discover`는 capability preflight에만 쓰며 business request의 선행 handshake가 아니다.

## 9. 역할별 Workflow handoff

### 9.1 FIRST_PROPOSAL

```text
Control API Claim Plan
→ EVIDENCE_PLAN AgentTask
→ validate planned read actions
→ MCP tools/call in parallel
→ validate structuredContent
→ EVIDENCE_ASSESS AgentTask
→ freeze EvidenceSnapshot
→ PROPOSE_INDEPENDENT and/or PROPOSE_FRANCHISE AgentTask
→ proposal support validation
→ deterministic finance·Gate·rank
→ CANDIDATE_AUDIT AgentTask
→ hard validator
→ reducer CAS
```

### 9.2 RESULT_FEEDBACK

```text
latest user input
→ INTENT_DELTA AgentTask
→ delta Schema and allowed field validation
→ before/after preview
→ user confirmation
→ Event and new State version
→ affected FIRST_PROPOSAL stages only
```

### 9.3 DOCUMENT_UPDATE

```text
validated parser blocks
→ DOCUMENT_EXTRACT AgentTask
→ Claim proposal and anchor validation
→ editable extraction form
→ one user batch apply
→ conflict detection
→ selective deterministic recompute
→ optional CANDIDATE_AUDIT
→ reducer CAS
```

## 10. Contract test와 완료 조건

양쪽 구현을 연결하기 전에 최소 다음 contract test를 공유한다.

| ID | Scenario | Pass condition |
| --- | --- | --- |
| `CP-001` | 정상 Proposal | `status=COMPLETE`, 정확한 role payload·지원 ref만 반환하고 expected result와 일치 |
| `CP-002` | full head 여덟 차원을 각각 하나씩 변경 | 모든 case에서 current State write 0 |
| `CP-003` | Agent가 pool 밖 id·Evidence id 생성 | `UNALLOCATED_OUTPUT_ID` 또는 `UNSUPPORTED_REFERENCE`로 폐기 |
| `CP-004` | schema-invalid output | 새 session repair에 이전 text·digest·validator error가 전달되고 2차 실패 후 partial commit 0 |
| `CP-005` | 같은 task 중복 완료 | 첫 valid result만 수용 |
| `CP-006` | Workflow 취소 뒤 결과 도착 | `LATE_DISCARDED`, current write 0 |
| `CP-007` | MCP scope token project 불일치 | 403, retrieval result 0 |
| `CP-008` | MCP `PARTIAL` | 전체 성공으로 표시하지 않음 |
| `CP-009` | 서울 Runtime·생성·embedding·reranker 독립 preflight 중 하나 실패 | `BLOCKED_BY_REGION`, Agent Workflow와 global 호출 0 |
| `CP-010` | 직접 Agent tool 호출 시도 | 실행 0, policy violation 기록 |
| `CP-011` | 고정 조건부 프랜차이즈 fixture | expected `NEXT_REVIEW_PRIORITY` rank와 primary review target이 정확히 일치 |
| `CP-012` | 문서 추출 폼 반영 전 | State·finance·Gate·rank 변경 0 |
| `CP-013` | 일곱 task type dispatcher matrix | 각 task가 정확한 child 하나만 실행하고 잘못된 author·복수 final·function part는 거절 |
| `CP-014` | `202` 뒤 API instance 강제 종료 | outbox redelivery로 재개되고 stage side effect는 정확히 한 번 |
| `CP-015` | same idempotency key의 same body·different body·concurrent duplicate | same은 같은 run, different는 409, concurrent는 run 하나 |
| `CP-016` | MCP discover·paginated list·10 tool call | revision·header·manifest·각 input/output Schema 통과 |
| `CP-017` | MCP JSON·SSE·cancel | 둘 다 같은 result, cancel 뒤 적용 0 |
| `CP-018` | MCP audience·identity·scope·project 공격 matrix | 전부 거절하고 project data 0 |
| `CP-019` | UNKNOWN·FRESH date·assumption coverage·money range·franchise eligibility 위반 | 명시된 semantic error code로 모두 거절 |
| `CP-020` | model·prompt·Schema·tool manifest 중 하나가 release와 다름 | release 승격 실패 |

완료 기준:

- `docs/contracts/*.schema.json` 전체가 Python `jsonschema` draft 2020-12와 Ajv 8 strict draft 2020-12의 date/date-time format 검증을 통과하고 공통 fixture 판정이 일치한다.
- task registry의 모든 payload Schema와 정상·기권 fixture가 존재한다.
- 열 개 MCP tool의 정상·부분·실패 fixture가 input/output Schema와 manifest 검증을 통과한다.
- 백엔드 fixture가 실제 Agent adapter 없이도 Workflow test를 통과한다.
- Agent fixture가 실제 database·MCP write 없이 role test를 통과한다.
- MCP client와 server가 `2026-07-28` conformance와 tool output validation을 통과한다.
- 배포 뒤 서울 Runtime·승인된 생성·embedding·reranker endpoint, IAM identity, runtime revision과 MCP service를 각각 read-back한다.

## 11. 버전 관리

- additive optional field: minor version
- required field, status 의미, tool 이름·Schema 변경: major version
- 설명·오탈자처럼 wire 동작이 같은 변경: patch version
- producer는 최소 한 minor 호환 기간 동안 직전 major를 읽을 수 있어야 한다. 보안상 제거가 필요한 계약은 예외로 즉시 차단한다.
- 요청과 결과는 반드시 동일 major version을 사용한다.
- prompt, model, payload Schema, tool Schema와 runtime revision은 release manifest에서 함께 pin한다.

## 12. 공식 근거와 확인 시점

- [Agent Runtime ADK 배포와 `async_stream_query`](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime/quickstart-adk)
- [Agent Runtime에서 ADK Agent 사용](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/use-an-adk-agent)
- [ADK app의 관리형 session 생성·삭제](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/use-an-adk-agent)
- [Agent Platform Runtime 지원 리전](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/agent-locations)
- [Gemini 3.5 Flash 모델별 지원 리전](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash)
- [Cloud Run service-to-service 인증](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
- [MCP 2026-07-28 Streamable HTTP 명세](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [MCP TypeScript SDK v2의 2026-07-28 지원](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28)
- [Cloud Run MCP server 배치](https://docs.cloud.google.com/run/docs/host-mcp-servers)

`accessed_at: 2026-08-21`. GCP API version, 지원 리전, SDK의 MCP revision과 실제 request shape는 배포 preflight에서 다시 확인한다.
