# CaffeMate AgentOps 운영 기준

## 실제 trace 경계

`FIRST_PROPOSAL`의 실제 동기 요청 경로는 다음과 같다.

```text
Control API
→ MCP / RAG
→ Agent Runtime
→ Vertex AI Gemini
```

Worker는 `FIRST_PROPOSAL` 실행 경로에 없다. Worker가 처리하는 Agent session cleanup과 dead-letter 작업은 각 요청에서 시작하는 독립 trace로 관측한다. 이 구분을 지키면 존재하지 않는 API→Worker 호출을 발표나 대시보드에서 암시하지 않는다.

각 AgentTask는 Control API에서 생성한 W3C `traceparent`를 포함한다. Agent Runtime은 그 context를 부모로 사용해 역할 실행 span을 만들고, Gemini `generateContent` 호출은 그 자식 span이 된다. 병렬 Proposal Agent 실행은 각각 별도 자식 span이지만 동일한 요청 trace에 속한다.

## 기록하는 값

- Agent 역할과 task type
- prompt version
- input/output schema ID
- model ID
- 배포 source revision
- HTTP 상태, 실행 지연, provider token 수, 오류 상태
- MCP tool 이름

## 절대 기록하지 않는 값

- 사용자 자연어 원문
- 업로드 문서 또는 Evidence 본문
- 정확한 지역명, 주소, 좌표
- 사용자·프로젝트·workflow·session·task 식별자
- 인증 토큰, credential, Secret Manager 값

## RAG 관측 계약

RAG 연결이 완성되기 전에는 0이나 임의 값을 내보내지 않는다. 실제 실행이 발생한 뒤에만 다음 측정값을 기록한다.

| 이름 | 도구 | 단위 | 허용 속성 |
|---|---|---|---|
| `caffemate.rag.retrieve.duration` | histogram | ms | `source_family`, `result_status`, `index_generation` |
| `caffemate.rag.rerank.duration` | histogram | ms | 동일 |
| `caffemate.rag.hits` | histogram | 1 | 동일 |
| `caffemate.rag.evidence.accepted` | counter | 1 | 동일 |
| `caffemate.rag.citations` | counter | 1 | 동일 |

질의문, 검색 결과 본문, Evidence ID는 metric label이나 span attribute로 넣지 않는다.

## GCP read-back

```bash
./scripts/deploy-agentops-observability.sh proj-aj20-211200020328
./scripts/verify-agentops-observability.sh proj-aj20-211200020328
```

첫 스크립트는 애플리케이션을 배포하지 않는다. 필요한 API와 비민감 로그 기반 지표, `CaffeMate AgentOps` Monitoring 대시보드만 생성하거나 갱신한다. 두 번째 스크립트가 원격 대시보드를 다시 읽어 필수 위젯과 개인정보 비수집 문구를 확인한다.

Cloud Trace exporter를 실행하는 서비스 계정에는 `roles/cloudtrace.agent`가 필요하다. 코드 배포 시 `CAFFEMATE_OTEL_ENABLED=true`와 `CAFFEMATE_SOURCE_REVISION=<git SHA>`를 명시하고, 새 revision에서 대표 `FIRST_PROPOSAL` 요청을 실행한 뒤 Trace Explorer에서 위 경로가 하나의 trace로 이어지는지 확인한다.

발표에서는 다음처럼 설명한다.

> Cloud Trace를 쓴 이유는 Agent 수를 보여주기 위해서가 아니라, 한 요청에서 검색·역할 추론·모델 호출 중 어디가 느리거나 실패했는지 같은 trace로 분리해 확인하기 위해서다. 사용자 입력과 근거 본문은 관측 데이터에 남기지 않고, prompt·schema·model·배포 버전만 기록해 결과를 재현할 수 있게 했다.
