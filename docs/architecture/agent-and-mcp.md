# Agent와 MCP Workflow

> 상태: draft
> 갱신일: 2026-08-21

## 원칙

- Multi-Agent는 역할 이름을 늘리는 기능이 아니다.
- Agent는 독립 reasoning context, tool 권한 또는 critic 역할이 있을 때만 둔다.
- Workflow controller가 실행 순서·재시도·종료를 결정한다.
- Agent와 MCP는 persistent State를 직접 수정하지 않는다.
- 돈·Gate·ranking은 deterministic code가 담당한다.

## Runtime 역할

| Role | Trigger | Input | Output | 금지 |
| --- | --- | --- | --- | --- |
| Intake Interpreter | 결과 이후 자연어·문서 intent | State summary + input | typed intent·delta proposal | 바로 State 변경 |
| Evidence Research Agent | 새 분석·Claim gap·stale | required Claims + read tools | Evidence candidates·missing·counterevidence | 계산·최종 추천 |
| Proposal Agent | frozen Evidence 준비 | Founder constraints + Evidence | typed candidates | 존재하지 않는 브랜드·근거 없는 비용·매출 생성 |
| Document Analyst | parsing 완료 | page/table anchors + schema | proposed Claims·risk flags | 법적 판단·자동 확정 |
| Typed Candidate Auditor | 계산 후보 생성 | candidate·Evidence·Calculation snapshot | violation·missing·Evidence request | 원 추천 작성·State write |

Orchestrator는 자유 토론 Agent가 아니라 typed Workflow controller다.

## CONFIRMED — GCP Runtime 배치

```text
Cloud Run Control API
→ IAM-authenticated Agent Runtime invocation
→ one ADK Multi-Agent application
→ allowlisted private MCP read tools
→ typed Agent output
→ Control API validation and State reducer
```

- 각 Agent 역할을 개별 Cloud Run service로 배포하지 않는다.
- Control API가 workflow, State version, 재시도 예산과 종료 조건을 소유한다.
- Agent Runtime은 reasoning과 ADK Agent 실행을 담당하지만 persistent State를 쓰지 않는다.
- Agent Runtime service identity는 private MCP 호출 권한만 가지며 원본 credential과 database write 권한을 갖지 않는다.
- 서울 리전에서 managed Agent Gateway가 지원되지 않으므로 Control API가 Agent Runtime을 직접 호출한다.
- Runtime·Sessions의 정식 지원과 별개로 선택한 model의 서울 리전 지원 여부를 배포 Gate에서 따로 검증한다.
- 생성·embedding model endpoint는 `asia-northeast3`만 허용하며 `global` fallback은 금지한다.

## 실행 Graph

### 첫 제안

```text
Evidence Research Agent
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

### Input Envelope

```yaml
workflow_run_id: required
project_id: required
state_version: required
policy_version: required
evidence_snapshot_id: required
task_type: required
input_payload: {}
allowed_tools: []
```

### Output Envelope

```yaml
status: COMPLETE | ABSTAIN | NEEDS_EVIDENCE | INVALID
schema_version: required
candidate_payload: {}
evidence_refs: []
missing_fields: []
counterevidence_refs: []
warnings: []
```

자유 문장 설명은 typed payload를 대체하지 못한다.

## MCP의 역할

MCP는 외부 자료와 project corpus를 호출하는 권한·schema 경계다. 추천 엔진이 아니다.

| Tool | Input | Output | Scope |
| --- | --- | --- | --- |
| `resolve_area` | 지명 또는 좌표 | 행정코드·경계·해석 후보 | public read |
| `get_area_profile` | 행정코드·기준일 | 인구·연령·사업체·카페 metrics | public read |
| `search_cafes` | 경계·업종·기준일 | raw rows·dedupe metadata | public read |
| `search_franchises` | 자금·규모·운영 조건 | 실제 브랜드·가맹 가능·missing | public read |
| `get_disclosure` | 브랜드·등록 identity | revision·field·anchor | public read |
| `get_official_procedure` | 지역·절차 | 관할·단계·source | public read |
| `retrieve_project_docs` | project id·Claim query | project document anchors | project read |

## MCP 공통 응답

```yaml
status: OK | PARTIAL | STALE | NOT_FOUND | ERROR
request_id: required
connector_version: required
evidence_records: []
missing_fields: []
conflicts: []
source_trace: []
```

## Tool 권한

- MCP service는 private Cloud Run으로 둔다.
- API와 Agent Runtime의 allowlist된 service identity만 invoke할 수 있다.
- connector credential은 MCP runtime에만 둔다.
- Agent에게 credential·database connection·raw secret을 전달하지 않는다.
- `retrieve_project_docs`는 user·project scope를 API가 서명한 context로 받는다.
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

- read tool retry는 connector별 최대 횟수와 timeout을 가진다.
- schema invalid Agent output은 한 번만 repair한다.
- 두 번째 실패는 `INVALID` 또는 `ABSTAIN`으로 종료한다.
- timeout 뒤 늦은 응답은 State version이 같을 때만 고려한다.
- tool 일부 실패를 전체 성공으로 숨기지 않는다.
- Evidence가 부족하면 Proposal은 후보 수를 억지로 채우지 않는다.

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

- required Claim coverage
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
