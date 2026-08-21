# CaffeMate Control API

Python/FastAPI가 공개 API, Identity Platform 토큰 검증, 권위 State reducer와
PostgreSQL transaction을 소유한다. Agent와 MCP는 이 저장소를 직접 수정하지 않는다.

## 로컬 실행

```bash
uv sync --project api --frozen
DATABASE_URL='postgresql+pg8000://...' uv run --project api caffemate-api migrate
FIREBASE_PROJECT_ID='...' DATABASE_URL='postgresql+pg8000://...' \
  uv run --project api uvicorn app.main:app --app-dir api --port 8080
```

`DATABASE_URL`이 없으면 Cloud SQL Connector용 설정을 사용한다.

- `INSTANCE_CONNECTION_NAME`
- `DB_USER`
- `DB_PASS`: 배포에서는 Secret Manager에서 주입
- `DB_NAME`
- `CLOUD_SQL_IP_TYPE`: `PRIVATE`, `PUBLIC`, `PSC` 중 하나이며 기본값은 `PRIVATE`

인증이나 PostgreSQL 설정이 없을 때 업무 API는 임시 저장소나 가짜 사용자를 사용하지
않고 `503`으로 실패한다. `/healthz`는 프로세스 liveness만 나타낸다. 마이그레이션은 API
시작 시 자동 실행하지 않고 배포 전 `caffemate-api migrate` 단계에서 명시적으로 실행한다.

## Backend 컨테이너

API와 Worker는 같은 dependency·계약 Schema를 담은 공용 이미지를 사용하고 서로 다른
Cloud Run service로 실행한다.

```bash
docker build -f deploy/backend.Dockerfile -t caffemate-backend:local .

# Control API
docker run --rm -p 8080:8080 caffemate-backend:local

# Worker
docker run --rm -p 8081:8080 caffemate-backend:local \
  sh -c 'exec uvicorn worker.main:app --host 0.0.0.0 --port "${PORT:-8080}"'
```

같은 이미지에서 migration job은 `caffemate-api migrate`를 실행한다. API와 Worker의 업무
endpoint는 필수 환경과 비밀값이 없으면 `503`으로 실패하며 `/healthz`만 liveness를 반환한다.

Worker stage ingress에는 `PUBSUB_SUBSCRIPTION`, `CONTROL_API_URL`,
`CONTROL_API_AUDIENCE`, `WORKER_ID`가 필요하다. DB outbox를 stage topic으로 전달하는
`POST /internal/v1/outbox:publish`에는 `WORKFLOW_STAGE_TOPIC_RESOURCE`도 필요하다. 두
endpoint는 public API가 아니며 private Cloud Run IAM 호출만 허용해야 한다.

Control API가 관리형 Agent Runtime의 `EVIDENCE_PLAN` 단계를 실행하려면 다음 설정이 모두
필요하다. 하나라도 없으면 Agent stage executor는 fail-closed 상태를 유지한다.

- `AGENT_RUNTIME_PROJECT_ID`
- `AGENT_RUNTIME_RESOURCE_ID`
- `AGENT_RUNTIME_USER_HMAC_SECRET`: Secret Manager에서 주입하는 32바이트 이상의 비밀값

`AREA_RESOLUTION`과 `EVIDENCE_RETRIEVAL`에서 private MCP를 호출하려면 다음 설정이 모두
필요하다. scope 비밀값은 API와 MCP에만 주입하며 Worker나 Agent Runtime에는 주입하지 않는다.

- `MCP_BASE_URL`
- `MCP_AUDIENCE`
- `MCP_SCOPE_HMAC_SECRET`: Secret Manager에서 주입하는 32바이트 이상의 비밀값

## Workflow 진행 조회

프론트엔드는 `GET /v1/projects/{project_id}/workflows/{workflow_run_id}`를 polling한다.
응답은 기존 Workflow 식별값과 full head에 다음 정보를 함께 반환한다.

- `stages`: 각 Stage의 상태, 시도 횟수, reason code와 비식별 failure code
- `completed_stage_count`, `total_stage_count`: 화면 진행률의 결정론적 입력
- `current_stage_codes`: 현재 `READY`, `RUNNING`, `WAITING_FOR_HUMAN`인 Stage
- `human_review_requests`: 사용자 확인이 필요한 Stage와 reason code
- `terminal_reason_codes`: 실패·시간 초과·기권을 설명하는 기계 판독 코드
- `poll_after_ms`: `QUEUED` 또는 `RUNNING`일 때 다음 조회 권장 간격이며, 그 외에는 `null`

프론트엔드는 Stage 이름이나 reason code로 권위 판단을 다시 계산하지 않는다. 표시 문구만
매핑하며, 현재 결과는 별도의 `GET /v1/projects/{project_id}/result`에서 조회한다.

결과 응답은 ResultBundle이 만들어질 때 고정한 `head`와 조회 시점의 `current_head`를 함께
반환한다. 여덟 차원 중 하나라도 다르면 `freshness`는 `STALE`이며,
`stale_head_dimensions`에 달라진 차원을 표시한다. 프론트엔드는 `STALE` 결과를 숨기거나
최신 결과로 표현하지 않고, 새 Workflow 진행 상태와 함께 이전 참고 결과로 표시한다.

## 결과 피드백 preview

`POST /v1/projects/{project_id}/feedback/previews`는 current Result가 있을 때만 자연어 입력을
받는다. Control API는 current State·Result·full head를 고정한 `INTENT_DELTA` Task를 만들고,
Agent가 제안한 operation id·field path·기존 값·타입·영향 Workflow를 검증한 뒤 durable
preview를 저장한다. 같은 `Idempotency-Key`와 같은 입력은 같은 preview를 반환하며, 다른
입력은 `409`로 거절한다.

preview 생성과 조회는 `venture_states`, `project_events`, 계산, current Result pointer를
변경하지 않는다. Agent 실행 중 full head가 달라지면 preview는 `EXPIRED`가 되며 적용할 수
없다. `REVIEW_REQUIRED` 응답에는 `before_founder`, `after_founder`, operation, 영향받는 후보와
Stage, `proposal_digest`가 포함된다.

`POST /v1/projects/{project_id}/feedback/{preview_id}/confirm`은 preview의 full head와
`proposal_digest`를 다시 받는다. 둘 중 하나라도 current 값과 다르면 `409`를 반환한다. 성공하면
하나의 트랜잭션에서 `FEEDBACK_CHANGE_CONFIRMED` Event, 새 Venture State, selective
`FIRST_PROPOSAL` run, 첫 Stage Outbox, preview의 `CONFIRMED` 상태를 함께 저장한다. 영향받지 않은
성공 Stage 결과는 이전 run에서 재사용하고, 영향받은 하위 DAG만 Worker가 다시 실행한다.

`POST /v1/projects/{project_id}/feedback/{preview_id}/cancel`은 preview만 `CANCELLED`로 바꾸며
State, Event, Workflow, Result에는 쓰지 않는다. 두 명령 모두 `Idempotency-Key`가 필요하다.

## 후보 선택

`POST /v1/projects/{project_id}/candidate-selections`는 current Result의 후보 하나를 실제 검토
대상으로 선택한다. 요청은 `result_bundle_id`, `candidate_id`, `expected_head`와
`Idempotency-Key`를 포함한다. 후보가 current Result에 없거나 full head가 달라지면 `409`다.

성공하면 `CANDIDATE_SELECTED` Event와 새 Venture State를 원자적으로 저장하고 선택한
Venture Case를 `SELECTED`·`CANDIDATE`로 전환한다. 응답에는 점포·임대 조건·견적·자금 조건과
개인카페 또는 프랜차이즈별 필수 자료 체크리스트가 포함된다. `property_intake_enabled`와
`document_intake_enabled`는 실제 자료 입력을 열지만 `is_final_go_decision`은 항상 `false`다.

## 문서 업로드 수명주기

`POST /v1/projects/{project_id}/documents/uploads`는 후보가 선택된 프로젝트에만 10분짜리
Cloud Storage V4 PUT URL을 발급한다. API가 강제하는 object path는
`projects/{project}/documents/{document}/revisions/{revision}/source.{ext}`이며, 원본 파일명은
경로에 사용하지 않는다. 허용 형식은 PDF, JPEG, PNG, DOCX이고 최대 크기는 50 MiB다.

클라이언트는 `Content-Type`과 `x-goog-meta-caffemate-sha256` 헤더를 signed request에 그대로
포함한다. `POST /documents/uploads:complete`에서 API는 실제 object를 읽어 magic 기반 MIME,
크기, SHA-256을 다시 계산한다. 하나라도 다르면 `QUARANTINED`로 격리하며 다운로드와 parsing을
금지한다. 모두 맞으면 `SCAN_PENDING`으로 전환하고 `DOCUMENT_SCAN_REQUESTED` Outbox를 만든다.

내부 malware scanner는 service identity로
`POST /internal/v1/documents/{revision}:scan-result`를 호출한다. clean 결과만
`READY_FOR_PARSING`으로 전환하고 `DOCUMENT_PARSE_REQUESTED`를 발행한다. 감염 또는 의심 결과는
`QUARANTINED`로 남는다. 사용자 다운로드 URL은 5분만 유효하며 격리·삭제 문서에는 발급하지
않는다. 운영 구성에는 `DOCUMENT_BUCKET`이 필요하다.

Parser Worker는 service identity로
`POST /internal/v1/documents/{revision}:parser-result`에 revision이 고정된 ParserBlock과 anchor를
제출한다. Control API는 block id 중복, revision 교차, page·table anchor 형식을 검사하고 불변
block set을 저장한다. 최대 12개씩 `DOCUMENT_EXTRACT` Task로 나누며, Agent가 할당되지 않은
Claim id, 계약에 없는 Claim type, Parser가 제공하지 않은 anchor를 반환하면 전체 결과를
거절한다.

검증된 제안은 `PROPOSED` Claim으로만 저장되고 State·재무·Gate·순위에는 아직 반영되지 않는다.
`GET /v1/projects/{project_id}/documents/{revision}/extraction-form`은 추출값, 원문 anchor, 단위,
중요도, 경고와 미해결 필드를 한 화면용 폼으로 반환한다. 프롬프트 주입 flag가 있거나 Agent가
불확실하다고 표시한 값은 `REVIEW_REQUIRED` 또는 `UNRESOLVED`로 남긴다.

사용자는 같은 경로에 `PUT` 요청으로 여러 필드를 한 번에 수정하거나 `null`로 비울 수 있다.
이 편집도 State를 바꾸지 않는다. 현재 State version이 form의 예상 version과 달라지면 `409`로
거절한다. 실제 State 반영은 별도의 `반영하고 다시 계산` 명령에서만 수행한다.

`POST /v1/projects/{project_id}/documents/{revision}/extraction-form:apply`는 form digest와
State version을 함께 잠근다. 비어 있지 않은 값만 `CONFIRMED` Claim으로 승격하며, 같은 종류의
기존 문서 Claim과 값이 다르면 어느 쪽도 자동 선택하지 않고 `OPEN` conflict를 만든다. Event,
State revision, Claim, conflict와 `CALCULATE_GATE_RANK`부터 시작하는 선택적 재계산 Workflow는
하나의 PostgreSQL transaction으로 저장된다. 재계산기는 선택된 후보의 문서 Claim을 사용자 확인
값으로 우선 사용하며, 열린 충돌이 있는 비용 항목은 `UNKNOWN`으로 처리한다.

선택적 재계산 Workflow는 원본 Workflow와 원본 Result를 명시적으로 참조한다. 새 Result가
커밋되면 API가 개인카페 모델 id 또는 프랜차이즈 브랜드 id를 안정적인 후보 식별값으로 사용해
이전 결과와 비교한다. `GET /v1/projects/{project_id}/result`의 `decision_delta`에는 후보 추가·삭제,
순위와 검토 상태, 초기 필요 현금·월 고정비·손익분기 매출의 변화가 포함된다. 비교할 원본 Result가
없는 최초 결과에는 `decision_delta`가 `null`이다.

## Agent Runtime session 정리

동기 Agent 호출의 session 삭제가 실패하면 API는 `AGENT_SESSION_CLEANUP` Outbox를 남긴다.
private Worker의 `POST /internal/v1/agent-sessions:cleanup`은 이 항목만 별도로 lease하고, 설정에
고정된 서울 Runtime resource에서 session을 삭제한다. 404는 이미 삭제된 것으로 간주한다.
transport 오류는 최대 다섯 번까지 지수형 지연으로 다시 시도하고, 잘못된 payload·resource scope
또는 재시도 소진은 원문 오류를 저장하지 않은 안정적인 failure code와 함께 `DEAD_LETTER`로
보낸다. 운영 환경에서는 Scheduler가 IAM 인증으로 이 내부 endpoint를 주기적으로 호출해야 한다.
