# Agent와 MCP Workflow

> 상태: draft
> 갱신일: 2026-08-23

## 원칙

- Multi-Agent는 역할 이름을 늘리는 기능이 아니다.
- Agent는 독립 reasoning context, tool 권한 또는 critic 역할이 있을 때만 둔다.
- Workflow controller가 실행 순서·재시도·종료를 결정한다.
- Agent는 persistent State snapshot을 읽어 추론할 수 있지만 권위 State를 직접 수정하지 않는다. MCP도 read-only이며, 검증된 변경은 Control API reducer만 반영한다.
- 돈·Gate·ranking은 deterministic code가 담당한다.

## Runtime 역할

| Role | Trigger | Input | Output | 금지 |
| --- | --- | --- | --- | --- |
| Intake Interpreter | 결과 이후 자연어·문서 intent | State summary + input | typed intent·delta proposal | 바로 State 변경 |
| Evidence Research Agent | 조회 완료·Claim gap·stale | Claims + 검증된 MCP·RAG 결과 | Evidence candidates·missing·counterevidence | 도구 선택·계산·최종 추천 |
| Proposal Agent | frozen Evidence 준비 | Founder constraints + Evidence | typed candidates | 존재하지 않는 브랜드·근거 없는 비용·매출 생성 |
| Document Analyst | parsing 완료 | page/table anchors + schema | proposed Claims·risk flags | 법적 판단·자동 확정 |
| Typed Candidate Auditor | 계산 후보 생성 | candidate·Evidence·Calculation snapshot | violation·missing·Evidence request | 원 추천 작성·State write |

Orchestrator는 자유 토론 Agent가 아니라 typed Workflow controller다.

서비스 경계의 정확한 전송·권한·재시도 계약은 [백엔드·Agent Runtime 연결 계약](../contracts/agent-runtime-protocol.md)을 따른다.

## CONFIRMED — GCP Runtime 배치

```text
Cloud Run Control API
→ IAM-authenticated Agent Runtime invocation
→ one ADK Multi-Agent application
→ typed Agent output
→ Control API validation and State reducer

Cloud Run Control API
→ versioned deterministic Evidence Plan
→ validated bounded read action
→ private MCP read tool
→ validated structured result
→ next Agent task input
```

- 각 Agent 역할을 개별 Cloud Run service로 배포하지 않는다.
- Control API가 workflow, State version, 재시도 예산과 종료 조건을 소유한다.
- Agent Runtime은 reasoning과 ADK Agent 실행을 담당하지만 persistent State를 쓰지 않는다.
- 첫 구현에서 Agent Runtime은 MCP를 직접 호출하지 않으며 MCP invoke 권한, 원본 credential과 database write 권한을 갖지 않는다.
- 열 개 전체 Schema registry와 실제 production connector capability를 분리한다. MCP `tools/list`와 결정론적 Evidence Plan은 실제 배포된 세 connector만 사용한다. 미배포 tool이 필요한 Claim은 실행하지 않고 missing Claim으로 전달하며, Agent가 connector 실패를 근거 부족처럼 해석하게 만들지 않는다.
- Control API의 결정론적 계획기가 Claim 종류를 고정된 support·counter read action으로 변환하고
  allowlist·scope·날짜·인자·호출 상한을 검증한 뒤 MCP를 호출한다.
- Evidence Researcher는 실행·검증된 MCP·RAG 결과만 평가하며 read action을 생성하지 않는다.
- 서울 리전에서 managed Agent Gateway가 지원되지 않으므로 Control API가 Agent Runtime을 직접 호출한다.
- Runtime·Sessions의 서울 지원과 별개로 승인한 생성 model의 고정 endpoint 가용성을 배포 Gate에서 따로 검증한다.
- 생성 model endpoint는 `global`의 `gemini-3.7-flash`, embedding endpoint는 `asia-northeast3`로 고정하며 다른 위치로 fallback하지 않는다.
- `gemini-3.7-flash`는 2026-08-21 `global` 실제 호출을 통과하고 사용자 승인을 받아 pin했으며, 고정 위치 preflight가 실패하면 `BLOCKED_BY_REGION`이다.
- ADK root는 모델이 아니라 deterministic dispatcher다. `task_type`을 검증해 정확히 한 역할만 실행하며 역할 transfer와 Agent 간 호출을 허용하지 않는다.

## 실행 Graph

### 첫 제안

```text
Claim Plan
→ deterministic Evidence Plan
→ parallel MCP·RAG retrieval
→ Evidence Research Agent assessment
→ independent and franchise Proposal branches
→ deterministic Finance and Gate
→ Typed Candidate Auditor
→ reducer validation and commit
```

- Proposal은 frozen Evidence Snapshot을 입력으로 받는다.
- 개인·프랜차이즈 branch는 같은 Founder State를 읽고 다른 schema로 병렬 실행할 수 있다.
- Auditor는 Proposal의 hidden reasoning이 아니라 결과·근거·계산만 본다.

### 문서 업데이트

```text
Document Analyst
→ Claim schema validation
→ editable extraction form generation
→ one batch apply action
→ deterministic conflict detection
→ selective recalculation
→ Typed Candidate Auditor
→ reducer commit
```

- OCR·추출값은 원문 anchor와 함께 한 폼에 자동 입력한다.
- 사용자는 값을 수정·삭제할 수 있고 필드별 확인 동작은 요구하지 않는다.
- 애매한 필드는 자동 입력하지 않고 `REVIEW_REQUIRED`로 남긴다.
- `반영하고 다시 계산` 한 번으로 현재 document·State version을 검증하고 폼 전체를 원자 적용한다.
- 일괄 적용 전 값은 State·finance·Gate·rank에 사용하지 않는다.

## Agent 공통 입출력

- 요청은 [Agent Task Schema](../contracts/agent-task.schema.json)를 따른다.
- 결과는 [Agent Task Result Schema](../contracts/agent-task-result.schema.json)를 따른다.
- `runtime_tool_policy`는 첫 구현에서 항상 `NO_DIRECT_TOOL_CALLS`다.
- 모델은 역할 payload와 status·근거 참조만 생성한다. Runtime이 검증된 요청에서 `task_id`, `invocation_id`, venture project, full head, input digest와 output Schema를 결합하고 Control API가 다시 대조한다.
- 자유 문장 설명은 typed payload를 대체하지 못한다.

## MCP의 역할

MCP는 외부 자료와 project corpus를 호출하는 권한·schema 경계다. 추천 엔진이 아니다.

- protocol revision은 `2026-07-28`, transport는 private Cloud Run의 stateless Streamable HTTP다.
- Control API가 유일한 production MCP client다.
- `structuredContent`만 tool output Schema로 검증해 다음 단계 입력으로 사용한다.

고정 registry는 [MCP Tool Manifest](../contracts/mcp-tool-manifest.json), 각 input·output은 [MCP Tool Contract Schema](../contracts/mcp-tool-contracts.schema.json), 현재 광고 가능한 부분집합은 [Production Capability](../contracts/mcp-production-capabilities.json)가 권위값이다.

| Tool | Input | Output | Scope |
| --- | --- | --- | --- |
| `resolve_area` | 지명·국가·limit | 행정코드·경계·해석 후보 | public read |
| `get_area_profile` | 행정코드·경계 version·기준일 | 인구·연령·사업체 metrics | public read |
| `search_cafe_observations` | 행정코드·metric·기준일 | 카페·매출·유동 관측 | public read |
| `search_business_events` | 행정코드·기간·event type | 개업·폐업·상태변경 | public read |
| `list_franchise_universe` | 카페 업종·기준일 | 전체 brand universe·개인 가맹 확인 상태 | public read |
| `get_franchise_disclosure` | brand id·기준일 | 정보공개서 field·anchor | public read |
| `retrieve_official_documents` | query·공식 source family·기준일 | 공식 문서 anchor | public read |
| `retrieve_project_documents` | query·허용 document revision | project 문서 anchor | project read |
| `get_official_procedure` | 관할·절차·기준일 | 관할·단계·source | public read |
| `get_source_health` | source id·기준일 | 갱신·장애·stale 상태 | public read |

## MCP 공통 응답

```yaml
status: OK | PARTIAL | STALE | NOT_FOUND | ERROR
request_id: required
connector_version: required
project_id: signed scope와 동일한 required value
evidence_records: []
missing_fields: []
conflicts: []
source_trace: []
```

## Tool 권한

- MCP service는 private Cloud Run으로 둔다.
- Control API의 allowlist된 service identity만 invoke할 수 있다.
- connector credential은 MCP runtime에만 둔다.
- Agent에게 credential·database connection·raw secret을 전달하지 않는다.
- `retrieve_project_documents`는 user·venture project scope를 API가 서명한 5분 이내 context로 받으며 project id를 tool argument로 받지 않는다.
- write tool은 현재 제공하지 않는다.

## 금지된 MCP Tool

- 계약 체결
- 송금·결제
- 대출 신청
- 본사·중개인 자동 연락
- 정부 신고·등록 제출
- legal safety 판정
- Candidate State 직접 write

## Retry와 실패

- 동네 검색은 2026년 3월 1일 기준 행정안전부 법정동 디렉터리를 MCP 이미지에서 먼저 조회한다.
  상세 주소 보조 조회만 외부 도로명주소 API에 의존한다.
- read tool retry는 connector별 최대 횟수와 timeout을 가진다.
- 완결된 model output의 JSON/Schema와 model-owned 의미·참조 오류는 validator error와 task-derived `generation_constraints`를 사용해 한 번만 repair한다. 남은 logical deadline이 2초 미만이면 repair를 시작하지 않고 provider generation도 그 deadline에 맞춰 취소한다.
- 가변 ID pool은 `generation_constraints`에 exact closed set으로 전달한다. production 크기의 nested ID enum은 Vertex가 HTTP 400 `INVALID_ARGUMENT`으로 거절하므로 provider response Schema에는 검증된 저복잡도 enum과 배열 상한만 둔다.
- transport·safety·full-head/echo 오류는 repair하지 않고, 두 번째 model-output 실패 뒤에는 추가 생성 없이 해당 역할을 실패·기권 처리한다.
- timeout·cancel 뒤 늦은 응답은 full head가 같아도 폐기한다. 그 외 응답도 full head 여덟 차원이 모두 current와 같을 때만 고려한다.
- tool 일부 실패를 전체 성공으로 숨기지 않는다.
- Evidence가 부족하면 Proposal은 후보 수를 억지로 채우지 않는다.
- 배포 canary는 브라우저에서 사용자가 서명된 지역 후보를 선택한 뒤와 같은 구조화 `AreaState`를
  사용한다. 주소 공급자 상태와 Agent 연쇄 실행 검증을 한 실패 원인으로 합치지 않는다.

## Observability

모든 run에 다음을 남긴다.

- workflow·State·Policy·prompt version
- model name과 inference configuration
- tool request id와 latency
- retrieved Evidence ids
- schema validation 결과
- retry·timeout·abstention
- token·cost

민감한 원문, exact coordinate, 사용자 자유입력 전문과 secret은 일반 log에 남기지 않는다.

## Agent 평가

### Evidence Research

- assessed Claim coverage
- unsupported source rate
- stale·scope mismatch detection
- counterevidence recall

### Proposal

- schema pass
- 실제 브랜드 여부
- constraint violation
- unsupported Claim rate
- 적격 후보가 부족할 때 기권

### Document Analyst

- field exact match
- 숫자·단위·표 header 연계
- page/table anchor correctness
- uncertain extraction escalation

### Typed Candidate Auditor

- missing cost recall
- Hard violation recall
- false alarm rate
- unsupported confidence detection

## Course 시연과 제품 runtime

수업에서 Multi-Agent 흐름을 보여주더라도 합성 fixture 결과를 실제 제품 품질 증거로 사용하지 않는다. 제품 성능 주장은 실제 source·sealed fixture·정량 평가 범위로 제한한다.
