# CaffeMate 백엔드·Agent Runtime 연결 계약

> 상태: active implementation contract
>
> 계약 버전: `1.2.0`
>
> 제품 정본: [제품 명세](../product-spec.md)
>
> 기술 기준: [Agent·RAG 런타임 상세 계약](../product-very-spec.md)
>
> 갱신일: 2026-08-24

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
    api --> planner[Deterministic Evidence Planner]
    planner -->|validated bounded read action| mcp
    api -->|GCP authenticated run| runtime[Agent Runtime ADK App]
    mcp[Private MCP]
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
- Control API는 Claim 종류별 버전형 규칙으로 support·counter read action을 결정한다. Agent가
  도구, 지역 코드, 날짜 범위, query 상한을 선택하지 않는다.
- 동일한 Claim Plan과 MCP manifest에는 같은 action plan과 digest가 생성되어야 한다.
- Control API가 allowlist와 typed arguments를 검증해 MCP를 실행하고 결과를 Evidence
  Researcher의 `ASSESS` 입력으로 전달한다.

이 경계로 Agent의 잘못된 tool 선택이 권한 실행으로 바로 이어지는 것을 막고, MCP 호출을 같은 Workflow trace와 project fence 안에서 재현할 수 있게 한다.

## 4. Agent Runtime 물리 전송

### 4.1 현재 배포 가능 상태

`asia-northeast3` Agent Runtime 배치는 유지한다. 생성 모델은 사용자 승인에 따라 `global` endpoint의 `gemini-3.7-flash`로 고정하며, 2026-08-21 `global` `generateContent` 실호출에서 HTTP 200과 `STOP` 응답을 확인했다. RAG Engine과 embedding은 계속 `asia-northeast3`를 사용한다.

- 서울 Runtime을 다른 리전으로 자동 변경하지 않는다. Agent Runtime 자체를 `global`에 배치하지 않는다.
- `global`은 승인된 생성 위치이며 fallback이 아니다. 생성 실패 시 regional endpoint로 조용히 전환하지 않는다.
- Runtime 생성, `global` 생성 모델 호출, 서울 embedding 호출, 서울 reranker 호출을 서로 독립된 배포 preflight로 실행한다.
- 네 항목 중 필수 항목 하나라도 고정된 위치에서 실패하면 Agent Workflow를 시작하지 않고 대체 위치 호출 수가 0임을 검증한다.
- release manifest에는 Runtime region과 generation region을 분리해 pin한다.
- release manifest는 운영 검색에 사용할 `ACTIVE IndexGeneration`의 exact RAG corpus resource, ACTIVE RAG file set, parser·index schema revision, embedding model, reranker, source revision set과 sealed evaluation digest를 함께 pin한다. 배포 preflight는 display name으로 corpus를 재탐색하지 않고 이 exact resource와 file set을 read-back한다.
- RAG gold set과 수치 Gate가 고정되기 전 `sealed_evaluation_digest`는 `docs/evaluation/high-value-cases.yaml`의 provisional sealed evaluation input identity를 pin한다. 이 digest 자체를 성능 통과 증거로 해석하지 않으며, 현재 release 승격에는 exact corpus/file read-back과 실제 retrieval·rerank preflight 성공이 별도로 필요하다.
- prompt와 Agent payload contract의 content digest는 소스에서 계산할 뿐 아니라 배포된 Runtime의 preflight 전용 `async_get_release_identity`를 호출해 artifact 내부 값도 read-back한다. manifest·source·Runtime 중 하나라도 다르면 release 승격을 막는다.
- private MCP도 release manifest에 Cloud Run service name·region·40자 source revision·immutable image digest를 함께 pin하고, Agent GCP preflight가 Cloud Run v2 service를 직접 GET해 template label과 단일 container image를 authoritative read-back한다. protocol/tool manifest가 같아도 runtime artifact가 다르면 release 승격을 막는다.

### 4.2 GCP endpoint와 관리형 session

Control API의 물리 설정은 `gcp_project_id`와 `resource_id`를 분리한다. 제품의 `venture_project_id`를 GCP endpoint 조립에 사용하지 않는다.

한 invocation은 Control API에서 Agent Runtime으로 한 번의
`async_ephemeral_stream_query`만 호출한다. Runtime adapter가 그 요청 안에서 다음 순서를 지킨다.

1. Control API가 지정한 비식별 session id로 ADK 관리형 session을 생성하고 반환 id를 검증한다.
2. 정확히 그 session에서 한 AgentTask를 실행하고 terminal event까지 스트리밍한다.
3. 성공·Agent 오류·사용자 취소 모두에서 generator `finally`가 session을 삭제한다.
4. 삭제가 끝나야 stream을 닫는다. 삭제 실패는 `RUNTIME_SESSION_CLEANUP_FAILED`로 명시하고
   Control API가 deterministic session id를 durable cleanup outbox에 넣어 멱등 재시도한다.

이 결합은 Agent 응답을 기다리지 않는 비동기 우회가 아니다. Agent 기능을 호출한 Control API는
final event와 Runtime 내부 session 삭제 완료까지 기다린다. 단지 동일 Runtime에 대한 외부 HTTPS
왕복을 `create → stream → delete` 세 번에서 한 번으로 줄인다. 서로 다른 역할은 별도 invocation id와
session id를 사용하므로 독립적인 추론 문맥을 유지한다.

운영 검증은 Control API identity로 ephemeral stream을 실제 호출해 session 생성, Agent 실행,
typed final 검증과 삭제가 모두 끝난 경우에만 통과한다. 이 검증은 첫 제안 Workflow와 분리한다.
현재 `FIRST_PROPOSAL`의 `RUN_PROPOSAL`은 Agent Runtime을 호출하지 않으며, 자연어 피드백과 문서
추출처럼 Agent가 필요한 기능만 이 전송 계약을 사용한다.

`async_get_release_identity`는 사용자 invocation 경로가 아닌 배포 preflight 전용 read-only class method다. session을 생성하거나 제품 State를 읽고 쓰지 않으며, 배포 artifact가 직접 계산한 `prompt_bundle_digest`와 `agent_contract_bundle_digest`만 반환한다. Control API의 정상 Agent 호출 순서는 위 세 class method만 사용한다.

```text
POST https://asia-northeast3-aiplatform.googleapis.com/v1/projects/{gcp_project_id}/locations/asia-northeast3/reasoningEngines/{resource_id}:streamQuery?alt=sse
class_method=async_ephemeral_stream_query
```

session 생성 입력의 `user_id`는 실제 사용자 식별자가 아니라 `venture_project_id`를 서버 비밀값으로 HMAC한 `p-<digest>`다. Control API는 `invocation_id`의 SHA-256 digest로 충돌하기 어려운 비식별 session id를 생성해 ephemeral stream에 전달하고 Runtime이 그 값으로 session을 만들었는지 확인한다. 이 값은 stream 응답이 deadline 때문에 유실돼도 같은 session을 durable cleanup 대상으로 지정하기 위한 식별자이며 제품 State나 사용자 식별자가 아니다. 별도 Sessions REST API와 Runtime adapter를 혼용하지 않으며 session TTL을 정상 삭제의 대체 계약으로 주장하지 않는다. 모든 `AgentTask`는 session 이력 없이 완전해야 하고 session은 제품 State나 대화 기억이 아니다.

`:streamQuery` body는 다음과 같다.

```json
{
  "class_method": "async_ephemeral_stream_query",
  "input": {
    "user_id": "p-<venture-project-hmac>",
    "session_id": "<invocation-id-derived ephemeral id>",
    "message": "<RFC 8785 canonical AgentTask JSON>"
  }
}
```

관리형 Runtime의 stream 응답은 배포 방식에 따라 `data: <event>` SSE line 또는
`{"output": <event>}` newline JSON envelope로 전달될 수 있다. Control API adapter는 줄마다
JSON object 하나만 허용하고 `output` object가 있으면 한 단계만 벗긴 뒤 동일한 final event
검증을 적용한다. 임의의 중첩 envelope, JSON이 아닌 line과 여러 final event를 정상 결과로
간주하지 않는다.

### 4.3 결정론적 root dispatcher

ADK application의 root는 LLM Agent가 아니라 custom `BaseAgent`인 `CAFFEMATE_TASK_DISPATCHER`다. 다음 매핑을 코드 상수와 release manifest로 함께 고정한다.

```text
INTENT_DELTA                         → INTENT_INTERPRETER
EVIDENCE_ASSESS                     → EVIDENCE_RESEARCHER
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

- Control API 전용 service account가 OAuth access token으로 한 `:streamQuery` endpoint를 호출한다.
- Runtime adapter만 자체 관리형 session을 생성·삭제하고, 배포 service account에는 대상 Runtime query에 필요한 최소 권한만 부여한다.
- 사용자 token, MCP credential, database credential과 raw secret을 Agent 입력에 넣지 않는다.
- runtime의 Agent identity에는 모델 호출과 자체 session 외에 MCP, Cloud SQL, BigQuery, Cloud Storage, Secret Manager 권한을 주지 않는다.

전송 adapter는 `AgentTask` 검증·digest 재계산, session 수명주기, IAM 호출, final event 선택, JSON parsing, `AgentTaskResult`와 의미 규칙 검증을 담당한다. 모델에는 역할 수행에 필요한 `task_type`, payload, input artifact, 허용 tool catalog와 repair context만 투영한다. task·invocation·project·full head·digest·output Schema 같은 불변 envelope는 모델이 생성하지 않으며 Runtime이 검증된 `AgentTask`에서 결합한다. session event 전문은 일반 log에 남기지 않고 trace id·latency·status·digest만 남긴다.

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
tool_manifest_digest: null for active Agent tasks
payload: typed role input
trace_context: optional W3C trace context
```

### 5.1 Task registry

| `task_type` | `agent_name` | 입력 payload | 출력 payload | 최대 실행시간 |
| --- | --- | --- | --- | ---: |
| `INTENT_DELTA` | `INTENT_INTERPRETER` | current State projection, latest input, field ontology | delta proposal | 30초 |
| `EVIDENCE_ASSESS` | `EVIDENCE_RESEARCHER` | Claims, bounded validated MCP·RAG candidates | Evidence assessments·conflicts | 60초 |
| `PROPOSE_INDEPENDENT` | `PROPOSAL_AGENT` | Founder·Area snapshot, registered model seeds, Evidence | independent candidate proposals | 60초 |
| `PROPOSE_FRANCHISE` | `PROPOSAL_AGENT` | Founder·Area snapshot, verified franchise universe, Evidence | franchise candidate proposals | 60초 |
| `DOCUMENT_EXTRACT` | `DOCUMENT_ANALYST` | extraction contract, parser blocks, anchors | proposed Claims·risk flags | batch당 60초 |
| `CANDIDATE_AUDIT` | `TYPED_CANDIDATE_AUDITOR` | frozen candidate·Evidence·calculation·Gate | audit findings | 60초 |

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

모델의 provider response Schema는 아래 항목 중 `status`, `payload`, `evidence_refs`,
`missing_claim_ids`, `reason_codes`, `warnings`만 허용한다. Runtime-owned envelope field를 모델이
반환하면 `MODEL_SEMANTIC_ENVELOPE_INVALID`로 거절한다. Runtime은 검증된 요청에서 나머지 필드를
결정론적으로 결합한 뒤에만 아래 외부 `AgentTaskResult`를 만들고 전체 Schema·의미 검증을 수행한다.
따라서 아래의 `echoed`는 “LLM이 복사했다”는 뜻이 아니라 “Runtime이 수용한 요청과 byte-equivalent한
값만 외부 결과에 존재한다”는 뜻이다.

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
→ semantic-only model output, Runtime envelope hydration
→ AgentTaskResult Schema
→ task·invocation·agent·type·venture project·full head·digest echo
→ registered role payload Schema
→ Evidence and artifact reference subset
→ semantic support validator
→ product Guardrail
→ downstream deterministic calculation
```

- 외부 결과의 echo 또는 full head mismatch는 repair하지 않고 `FENCE_MISMATCH`로 폐기한다. 정상 Runtime 경로에서는 모델이 이 필드를 생성하지 않으므로 mismatch 자체가 Runtime·transport 계약 위반이다.
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

Agent 호출은 side effect가 없으므로 동일 `task_id`가 둘 이상 실행돼도 State를 바꾸지 않는다.
Control API는 `(task_id, input_digest)`와 current full head를 검증하여 현재 요청의 첫 valid result만
수용하고 나머지는 폐기한다. 첫 제안 Worker나 stage compare-and-swap은 이 경계에 존재하지 않는다.

### 7.2 재시도 표

| 실패 | 재시도 | 처리 |
| --- | ---: | --- |
| network, HTTP 408·429·5xx | 최대 2회 | 새 `invocation_id`, 같은 `task_id`·digest, `transport_attempt` 증가 |
| JSON parse·Schema 실패 | repair 1회 | 새 invocation에 이전 response text·digest와 제한된 validator error를 `repair_context`로 전달 |
| safety block | 0회 | `SAFETY_BLOCKED` |
| HTTP 400·401·403 | 0회 | 설정·권한 오류로 실패 |
| fence·ACL·unsupported ref | 0회 | 즉시 폐기 |
| deadline 초과 | 0회 | `TIMED_OUT`, session stream 종료, 늦은 결과 폐기 |

transport backoff는 250ms, 750ms이고 invocation id에서 파생한 0~100ms jitter를 더한다. 429의 `Retry-After`는 2초 이하이면서 남은 deadline 안에 있을 때만 우선한다. Runtime 내부 session 생성, run, 삭제, response validation, repair와 cleanup enqueue까지 모두 `deadline_at` 예산에 포함하며 각 호출 직전에 남은 시간이 2초 미만이면 재시도하지 않는다. ephemeral stream은 현재 남은 logical deadline에서 durable cleanup enqueue용 2초를 제외한 값만 timeout으로 사용한다. 따라서 60초 task가 transport의 30초 상한으로 조용히 잘리지 않는다. stream이 중단되거나 Runtime이 삭제 실패를 명시하면 Control API는 해당 invocation의 deterministic session id를 cleanup outbox에 넣고 원래 실패를 보존한다.

repair는 같은 session 이력에 의존하지 않는다. `repair_context`에 직전 응답 text, 그 SHA-256 digest와 최대 50개 validator error가 반드시 들어간다. 두 번째 schema 실패는 기권으로 끝나며 세 번째 생성 호출은 없다.

Agent invocation이 호출자 연결 종료나 내부 운영 취소로 폐기된 뒤 도착한 결과는 full head가 같아도
적용하지 않는다. 그 외 결과도 current full head의 여덟 차원이 모두 요청과 같을 때만 수용한다.
동기식 첫 제안에는 공개 취소 동작이 없다.

### 7.3 단일 첫 제안 실행

공개 `POST /v1/projects/{venture_project_id}/workflows/FIRST_PROPOSAL`은 Control API가 다음 작업을
한 transaction에서 완료한 뒤 `workflow_run_id`를 반환한다.

1. 현재 Venture State와 project head를 잠근다.
2. 등록 후보와 수용된 Evidence로 제안, 재무 계산, Gate와 순위를 실행한다.
3. `workflow_run`, 단일 `RUN_PROPOSAL` 기록, 결과 bundle과 idempotency 응답을 함께 저장한다.

이 경로에는 Outbox 발행, Pub/Sub, Worker lease, heartbeat와 내부 stage 실행 endpoint가 없다.
HTTP 응답 코드는 기존 React 계약 때문에 현재 `202`를 유지하지만, 반환되는 Workflow는 이미
`SUCCEEDED`이며 진행 조회는 완료 상태를 한 번 읽는다.

```text
POST /v1/projects/{venture_project_id}/workflows/FIRST_PROPOSAL
GET  /v1/projects/{venture_project_id}/workflows/{workflow_run_id}
GET  /v1/projects/{venture_project_id}/result
```

세 공개 동작은 `FirstProposalService`의 run, progress, result 진입점으로 모은다. React는 내부
모듈, Agent Runtime, MCP와 운영 검증 endpoint를 직접 호출하지 않는다. Worker의 `/internal/**`
경로는 Agent session cleanup과 운영 실패 처리 전용이며 Worker service identity를 검증한다.

실행 전제조건, State 무결성이나 저장 transaction이 실패하면 결과와 Workflow 기록을 함께
rollback한다.

## 8. MCP 연결 계약

### 8.1 Protocol과 transport

- protocol revision: `2026-07-28`
- endpoint: private Cloud Run의 `POST /mcp`
- transport: stateless Streamable HTTP
- encoding: UTF-8 JSON-RPC 2.0
- 사용 기능: `server/discover`, `tools/list`, `tools/call`
- 사용하지 않는 기능: write tool, prompts, sampling, elicitation, persistent MCP session, Tasks extension
- implementation: MCP server는 공식 TypeScript SDK v2의 `createMcpHandler(..., { legacy: 'reject' })`, FastAPI Control API는 공식 Python SDK v2 client를 사용한다. 양쪽 package version을 lockfile·release manifest에 pin하며 hand-written transport와 2025 fallback은 허용하지 않는다.

현재 production connector 범위는 [Production Capability](./mcp-production-capabilities.json)에 고정한다. `resolve_area`는 행정안전부 도로명주소 검색 API와 버전 고정 법정동 목록을 사용한다. `get_area_profile`과 `search_cafe_observations`는 승인된 BigQuery grounding snapshot만 조회한다. `retrieve_official_documents`는 서울 Vertex AI RAG Engine의 승인 official corpus만 조회한다. `list_franchise_universe`는 2026-08-23에 브랜드 공식 가맹 안내·창업상담 페이지를 확인한 snapshot만 반환한다. 이 값은 개인의 일반적인 가맹 문의 가능성을 뜻하며 특정 후보 지역의 출점 승인이나 정보공개서 완전성을 뜻하지 않는다. 따라서 각 브랜드에는 `area_availability_hq_confirmation`과 `franchise_disclosure`를 missing context로 남기고 `조건부 검토`로만 사용할 수 있다. `retrieve_project_documents`, `get_franchise_disclosure` 등 connector가 없는 tool은 MCP client에게 호출 가능하다고 표시하지 않는다.

RAG connector의 `data`는 검색 hit이고 그 자체가 확정 Evidence가 아니다. Control API는 hit의 Claim,
지리 범위, 기준일, document revision, anchor, excerpt와 `source_trace`를 결합해 `EvidenceRecord` 후보를
만든다. 원문과 정확히 연결할 수 없는 hit는 수용하지 않는다. 별도 근거 수집 또는 문서 적용 경로가
후보의 관계, 범위, 날짜와 권위를 검증한 뒤에만 accepted Evidence로 저장한다.

첫 제안의 공개 결과 경로는 `accepted Evidence → RUN_PROPOSAL → CandidateResult`다. 결과에는 출처
제목, 원문 주소, 기준일, 문서 revision, 인용문과 Evidence 참조를 보존한다. 필요한 공식 문서가
없으면 `official_document_gaps`로 노출하며 Agent가 문서 내용이나 확인 여부를 생성하지 않는다.

배포 단위는 `caffemate-mcp` Cloud Run service다. 서비스는 unauthenticated invoker를 허용하지 않고 `caffemate-api-runtime`만 `roles/run.invoker`를 가진다. MCP runtime identity는 official RAG 조회와 서울 Vertex AI Ranking API 호출에 필요한 최소 권한만 가진다. `aiplatform.endpoints.predict`, `aiplatform.ragCorpora.get`, `aiplatform.ragCorpora.query`, `aiplatform.ragFiles.get`, `discoveryengine.rankingConfigs.rank`의 retrieval/embedding/ranking read·execute 권한만 허용하고 mutation 권한은 허용하지 않는다. `ragFiles.get`은 검색 mutation이 아니라 release manifest에 고정된 공식 RAG 파일의 ACTIVE 상태와 identity를 health probe가 읽기 위한 권한이다. application boundary에서도 같은 service identity의 Google ID token, service URL audience, 최대 300초의 HMAC scope token을 모두 검증한다. Control API는 `MCP_BASE_URL`, `MCP_AUDIENCE`, `MCP_SCOPE_HMAC_SECRET` 세 설정이 모두 있을 때만 MCP client를 구성한다.

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

온보딩 이전 지역 검색도 브라우저가 MCP를 직접 호출하지 않는다. Control API는 먼저 프로젝트
소유권을 확인한 뒤 `area-lookup:<uuid>` 형식의 단기 operation id와 lookup 전용 head를 사용해
`resolve_area`를 호출한다. 이 operation id는 Workflow run이나 권위 State가 아니며, MCP의
project fence와 호출 추적에만 사용한다. 검색 결과를 브라우저에 전달할 때에는 프로젝트·검색어·
후보 identity·만료를 묶은 별도 서명 토큰을 발급한다. 온보딩 확정 시 해당 토큰을 재검증하고,
표시 문자열이나 브라우저가 다시 보낸 지역 코드를 권위값으로 신뢰하지 않는다.
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

tool 이름, input·output Schema와 version은 [MCP Tool Manifest](./mcp-tool-manifest.json)에 pin한다. 각 tool의 input·output 구조는 [MCP Tool Contract Schema](./mcp-tool-contracts.schema.json)가 권위값이다. 실제 배포가 광고해야 할 부분집합은 [Production Capability](./mcp-production-capabilities.json)가 권위값이며 이 파일은 전체 manifest digest도 함께 pin한다. server는 connector registry와 일치하는 tool만 등록하고 tool version을 `_meta["com.caffemate/toolVersion"]`에 반환한다.

`retrieve_official_documents`와 `retrieve_project_documents`의 production retrieval backend는 Vertex AI RAG Engine이다. Agent는 corpus resource name이나 metadata filter를 직접 전달하지 않는다. MCP server가 scope token의 `venture_project_id`, Cloud SQL의 허용 corpus·file mapping과 tool input의 document revision allowlist로 실제 검색 범위를 만든다. 공식 corpus와 project corpus를 한 요청에서 섞지 않으며, project mapping이 없거나 다르면 RAG 호출 전에 403으로 끝낸다.

인구·업소·개폐업·프랜차이즈 구조화 필드는 나머지 typed connector tool로 조회하며 문서 RAG context를 정형 수치의 최종값으로 사용하지 않는다. RAG retrieval hit도 바로 Evidence가 아니며 tool output Schema, 원문 anchor·source revision·scope·freshness 검사를 통과한 뒤에만 `evidence_records`에 들어간다.

배포 preflight는 pagination을 끝까지 소비한 `tools/list`의 name·version·inputSchema·outputSchema를 RFC 8785로 정규화한다. 관측 목록은 Production Capability의 세 tool과 정확히 비교하고, 각 Schema는 전체 manifest의 해당 definition과 비교한다. Production Capability가 pin한 전체 manifest digest도 [manifest digest](./mcp-tool-manifest.sha256)와 일치해야 한다. 운영 connector의 누락·추가, 미래 tool의 잘못된 광고, schema 차이 또는 digest 차이가 하나라도 있으면 `MCP_MANIFEST_MISMATCH`로 release 승격을 막는다. `server/discover`는 capability preflight에만 쓰며 business request의 선행 handshake가 아니다.

`McpManifestPreflight`는 배포 검증에서 Control API image의 runtime service account로 실행한다.
사용자 `FIRST_PROPOSAL` 시작 요청은 이 원격 점검이나 MCP 조회를 실행하지 않는다. Control API는
현재 State와 이미 수용된 Evidence를 읽어 단일 `RUN_PROPOSAL` 결과를 저장한다. MCP 장애는 근거 수집
기능에서 명시적으로 보고하며 첫 제안 요청 안에서 숨은 재시도나 대체 자료 조회를 만들지 않는다.

## 9. 역할별 Workflow handoff

### 9.1 FIRST_PROPOSAL

```text
current Venture State
→ registered independent models and verified franchise brands
→ accepted Evidence projection
→ deterministic candidate finance
→ capital Gate and next-review ranking
→ result bundle and single RUN_PROPOSAL record
```

현재 첫 제안은 Agent Runtime이나 MCP를 호출하는 DAG가 아니다. Control API가 등록 후보와 현재
accepted Evidence를 읽고 한 transaction에서 결과를 계산해 저장한다. 사용자가 개인카페만,
프랜차이즈만 또는 둘 다를 선택한 경우에도 같은 단일 실행 경로를 사용한다.

개인카페의 등록 기준과 프랜차이즈의 공식 비용·가정은 서로 다른 provenance로 보존한다. 실제 점포
조건이 들어오면 선택 후보의 보증금, 권리금, 월세와 관리비만 사용자 입력값으로 교체하고 재무,
Gate와 순위를 다시 계산한다. 프랜차이즈의 지역 출점 가능 여부나 정보공개서가 확인되지 않았으면
후보를 제거하지 않고 조건부 검토와 다음 확인 항목을 표시한다.

Proposal, Evidence Researcher와 Independent Critic의 typed task 계약은 삭제하지 않는다. 이 역할은
비정형 자료를 구조화하거나 별도의 품질 강화 실행을 추가할 때 사용할 수 있다. 그러나 해당 Agent
호출이 첫 결과 생성의 필수 조건이라고 문서화하거나, 삭제된 stage 이름을 첫 제안 Workflow에 다시
연결하지 않는다.

첫 제안에서 새 근거가 필요하면 검색을 요청 중에 반복하지 않는다. 공식 데이터 수집, 사용자 문서
적용 또는 명시적인 근거 갱신 경로가 Evidence를 먼저 저장하고, 이후 `RUN_PROPOSAL`이 현재
Evidence를 읽어 결과를 다시 만든다.

### 9.2 RESULT_FEEDBACK

```text
latest user input
→ INTENT_DELTA AgentTask
→ delta Schema and allowed field validation
→ before/after preview
→ user confirmation
→ Event and new State version
→ single RUN_PROPOSAL recompute
```

`INTENT_DELTA`는 자연어를 State 변경 제안으로 해석해야 하므로 Agent 역할을 유지한다. 다만
provider response Schema는 전체 공용 값 공간을 그대로 노출하지 않고 현재 task의
`allowed_field_paths`와 `operation_id_pool`로 제한한다. 피드백 State가 실제로 표현하는
`NULL`·`STRING`·`INTEGER`만 허용하고, operation·질문·위험·이유·경고 배열에는 입력 기반 상한을
둔다. Vertex가 중첩 operation-level `anyOf`를 생성 강제로 취급하지 않을 수 있으므로 field path,
operation kind, expected value와 typed value의 제한을 operation의 직접 property schema에 둔다.
여러 field의 kind·value 조합 관계는 semantic validator가 다시 검사한다. 이 제한은 값을 새로
만들거나 결과를 고치는 fallback이 아니라 모델이 선택할 수 있는 공간을 Control API의 실제 권한과
일치시키는 생성 최적화다. 최종 권위 검증은 기존 전체 JSON Schema, semantic validator,
`expected_old_value`, full-head fence가 계속 담당한다.

`source_span`은 `latest_user_input`의 Unicode code point 기준 0부터 시작하는 반열린 구간이다.
provider Schema가 입력 길이로 start·end 상한을 제한하고 semantic validator가 비어 있거나 입력
밖으로 벗어난 구간을 다시 거절한다.

관리형 Runtime 반복 검증에서 State 변경 성공을 요구하는 문장은 가능성이나 능력이 아니라 변경
의향을 명시해야 한다. 예를 들어 `대출을 받을 의향이 있습니다.`는 `NO → YES` 검증에 사용하지만,
`대출을 받을 수 있습니다.`는 자격·가능성 진술이므로 같은 기대값을 강제하지 않는다. 이 구분으로
모델의 합리적인 `CLARIFY`를 가짜 회귀로 판정하지 않는다.

Vertex가 `MAX_TOKENS` 또는 다른 불완전 finish reason을 반환하면 부분 JSON을 수용하거나 repair하지
않는다. 이는 같은 입력을 반복해도 회복되지 않는 terminal model-output failure이므로 Runtime은
HTTP 422로 분류하고 Control API는 transport retry를 실행하지 않는다. 408·429·5xx와 네트워크
장애만 새 invocation·session을 사용하는 bounded transport retry 대상이다. schema-valid text의
Schema·echo·의미 오류만 같은 Agent의 단일 validator-guided repair 대상이다.

### 9.3 DOCUMENT_UPDATE

```text
validated parser blocks
→ DOCUMENT_EXTRACT AgentTask
→ Claim proposal and anchor validation
→ editable extraction form
→ one user batch apply
→ conflict detection
→ Event and new State version
→ single RUN_PROPOSAL recompute
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
| `CP-006` | 폐기된 Agent invocation의 결과가 뒤늦게 도착 | `LATE_DISCARDED`, current write 0 |
| `CP-007` | MCP scope token project 불일치 | 403, retrieval result 0 |
| `CP-008` | MCP `PARTIAL` | 전체 성공으로 표시하지 않음 |
| `CP-009` | 서울 Runtime·global 생성·서울 embedding·서울 reranker 독립 preflight 중 하나 실패 | `BLOCKED_BY_REGION`, Agent Workflow와 대체 위치 호출 0 |
| `CP-010` | 직접 Agent tool 호출 시도 | 실행 0, policy violation 기록 |
| `CP-011` | 고정 조건부 프랜차이즈 fixture | expected `NEXT_REVIEW_PRIORITY` rank와 primary review target이 정확히 일치 |
| `CP-012` | 문서 추출 폼 반영 전 | State·finance·Gate·rank 변경 0 |
| `CP-013` | 일곱 task type dispatcher matrix | 각 task가 정확한 child 하나만 실행하고 잘못된 author·복수 final·function part는 거절 |
| `CP-014` | 첫 제안 transaction 중 API instance 종료 | Workflow·결과가 함께 rollback되고 중간 결과는 현재 결과가 되지 않음 |
| `CP-015` | same idempotency key의 same body·different body·concurrent duplicate | same은 같은 run, different는 409, concurrent는 run 하나 |
| `CP-016` | MCP discover·paginated list·10 tool call | revision·header·manifest·각 input/output Schema 통과 |
| `CP-017` | MCP JSON·SSE·cancel | 둘 다 같은 result, cancel 뒤 적용 0 |
| `CP-018` | MCP audience·identity·scope·project 공격 matrix | 전부 거절하고 project data 0 |
| `CP-019` | UNKNOWN·FRESH date·assumption coverage·money range·franchise eligibility 위반 | 명시된 semantic error code로 모두 거절 |
| `CP-020` | model·prompt content·Schema content·tool manifest·ACTIVE IndexGeneration 중 하나가 release와 다름 | release 승격 실패 |
| `CP-021` | 같은 Claim Plan을 반복 실행하고 Claim·allowlist·action budget을 변조 | 정상 입력은 byte-equivalent plan·digest, 변조 입력은 MCP 호출 전 계약 오류, Agent 호출 0 |
| `CP-022` | `INTENT_DELTA` 단순 변경·NOOP·CLARIFY와 provider `MAX_TOKENS` | 동적 Schema가 task pool과 배열 상한을 반영하고 정상 입력은 STOP, 불완전 출력은 생성 재시도 0 |

완료 기준:

- `docs/contracts/*.schema.json` 전체가 Python `jsonschema` draft 2020-12와 Ajv 8 strict draft 2020-12의 date/date-time format 검증을 통과하고 공통 fixture 판정이 일치한다.
- task registry의 모든 payload Schema와 정상·기권 fixture가 존재한다.
- 열 개 MCP tool의 정상·부분·실패 fixture가 input/output Schema와 manifest 검증을 통과한다.
- 백엔드 fixture가 실제 Agent adapter 없이도 Workflow test를 통과한다.
- Agent fixture가 실제 database·MCP write 없이 role test를 통과한다.
- MCP client와 server가 `2026-07-28` conformance와 tool output validation을 통과한다.
- 배포 뒤 서울 Runtime·`global` 승인 생성·서울 embedding·서울 reranker endpoint, IAM identity, runtime revision과 MCP service를 각각 read-back한다.

## 11. 버전 관리

- additive optional field: minor version
- required field, status 의미, tool 이름·Schema 변경: major version
- 설명·오탈자처럼 wire 동작이 같은 변경: patch version
- producer는 최소 한 minor 호환 기간 동안 직전 major를 읽을 수 있어야 한다. 보안상 제거가 필요한 계약은 예외로 즉시 차단한다.
- 요청과 결과는 반드시 동일 major version을 사용한다.
- prompt, model, payload Schema, tool Schema, Agent Runtime revision, private MCP runtime artifact와 ACTIVE IndexGeneration은 release manifest에서 함께 pin한다.
- prompt와 Agent payload contract는 symbolic version/id뿐 아니라 canonical content digest도 pin한다. 같은 id 아래 내용이 바뀌어도 release source seal이 실패해야 한다.
- manifest 자체에 수동 `VERIFIED` 상태를 기록하지 않는다. release 승격 가능 여부는 immutable pin과 현재 source seal, 실제 GCP preflight read-back 결과로 계산한다.

## 12. 공식 근거와 확인 시점

- [Agent Runtime ADK 배포와 `async_stream_query`](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime/quickstart-adk)
- [Agent Runtime에서 ADK Agent 사용](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/use-an-adk-agent)
- [ADK app의 관리형 session 생성·삭제](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/use-an-adk-agent)
- [Agent Platform Runtime 지원 리전](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/agent-locations)
- [Gemini 3.7 Flash 모델별 지원 리전](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-7-flash)
- [Cloud Run service-to-service 인증](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
- [MCP 2026-07-28 Streamable HTTP 명세](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [MCP TypeScript SDK v2의 2026-07-28 지원](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28)
- [Cloud Run MCP server 배치](https://docs.cloud.google.com/run/docs/host-mcp-servers)

`accessed_at: 2026-08-22`. GCP API version, 지원 리전, SDK의 MCP revision과 실제 request shape는 배포 preflight에서 다시 확인한다.
