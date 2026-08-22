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
않고 `503`으로 실패한다. `/health`는 프로세스 liveness만 나타낸다. 마이그레이션은 API
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
endpoint는 필수 환경과 비밀값이 없으면 `503`으로 실패하며 `/health`만 liveness를 반환한다.

Worker stage ingress에는 `PUBSUB_SUBSCRIPTION`, `CONTROL_API_URL`,
`CONTROL_API_AUDIENCE`, `WORKER_ID`가 필요하다. DB outbox를 stage topic으로 전달하는
`POST /internal/v1/outbox:publish`에는 `WORKFLOW_STAGE_TOPIC_RESOURCE`도 필요하다. 두
endpoint는 public API가 아니며 private Cloud Run IAM 호출만 허용해야 한다.

Worker는 Stage 처리 중 15초마다 lease heartbeat를 compare-and-swap으로 갱신하고 lease를 90초로
연장한다. heartbeat가 취소, stale head, 만료 또는 다른 Worker의 lease를 관측하면 늦은 결과를
checkpoint하거나 failure로 덮지 않고 즉시 폐기한다. 한 Stage의 Worker 처리 시간은 기본 120초로
제한하며 초과 시 `STAGE_TIMEOUT`으로 기록한다. timeout 뒤 백그라운드 호출이 늦게 반환해도
checkpoint 경로가 없고 만료된 lease token으로는 Control API가 결과를 수용하지 않는다.

Control API가 `EVIDENCE_ASSESS`, Proposal과 Candidate Audit처럼 실제 추론이 필요한 관리형
Agent Runtime 단계를 실행하려면 다음 설정이 모두 필요하다. 하나라도 없으면 해당 Agent stage
executor는 fail-closed 상태를 유지한다. `EVIDENCE_PLAN`은 Control API의 결정론적 코드로
실행되므로 Agent Runtime 설정이나 모델 호출을 요구하지 않는다.

`EVIDENCE_ASSESS`는 의미 판정을 담당하는 필수 Agent 단계다. Control API는 동일 물리 조회 결과를
중복 전달하지 않고 action별 상위 세 Evidence record만 투영한다. Runtime timeout, transport 또는
`MAX_TOKENS` 실패를 가짜 `ABSTAIN` 성공으로 바꾸지 않으며, 원래 code를 가진 Stage 실패로 남기고
조회 결과를 Evidence로 승격하지 않는다. 다른 모델·리전으로도 전환하지 않는다.

- `AGENT_RUNTIME_PROJECT_ID`
- `AGENT_RUNTIME_RESOURCE_ID`
- `AGENT_RUNTIME_USER_HMAC_SECRET`: Secret Manager에서 주입하는 32바이트 이상의 비밀값

`AREA_RESOLUTION`과 `EVIDENCE_RETRIEVAL`에서 private MCP를 호출하려면 다음 설정이 모두
필요하다. scope 비밀값은 API와 MCP에만 주입하며 Worker나 Agent Runtime에는 주입하지 않는다.

- `MCP_BASE_URL`
- `MCP_AUDIENCE`
- `MCP_SCOPE_HMAC_SECRET`: Secret Manager에서 주입하는 32바이트 이상의 비밀값

MCP client는 network·408·429·5xx에만 최대 두 번 다시 시도하며, 같은 논리 tool 호출의
JSON-RPC request id와 scope를 유지한다. 기본 지연은 250ms·750ms이고 `Retry-After`는 2초
이하일 때만 전체 timeout 예산 안에서 사용한다. 400·401·403, 계약 위반, project scope 불일치와
결과 Schema 오류는 다시 시도하지 않는다. 호출자가 W3C `traceparent`를 주지 않으면 project·
Workflow·tool·typed arguments에서 비식별 trace를 만들어 모든 물리 시도에 동일하게 전달한다.
`PARTIAL`, `STALE`, `NOT_FOUND`는 전송 성공과 별도의 domain 상태로 그대로 보존한다.

## 온보딩 지역 선택

`POST /v1/projects/{project_id}/areas:search`는 프로젝트 소유권을 확인한 Control API가 private
MCP의 `resolve_area`를 대신 호출한다. 응답 후보는 `AreaIdentity`와 15분짜리
`selection_token`을 포함한다. 주소 API의 10자리 `admCd`는 법정동 코드로 저장하며, 별도 근거가
없는 행정동 코드는 만들지 않는다. 검색 첫 페이지의 완전성도 확인되지 않았으므로
`completeness=UNVERIFIED`로 반환한다.

프론트엔드는 사용자가 후보 하나를 명시적으로 선택한 뒤 토큰을
`POST /v1/projects/{project_id}/onboarding/confirm`의 `area_selection_token`에 넣는다. 서버는
프로젝트·정규화 검색어·만료·서명을 다시 검증하고 구조화된 지역을 Founder 입력과 같은 Event에
저장한다. 이후 `AREA_RESOLUTION`은 확정 State를 재검색하지 않는다. 입력 문자열만 보낸 요청은
지역 검색이 구성되지 않은 개발·회귀 환경의 기존 경로만 유지하며, 배포 프론트엔드에서는 사용할
수 없다.

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

Agent Runtime 호출은 논리 Task의 `task_id`와 `input_digest`를 유지하면서 물리 호출마다 새
`invocation_id`와 새 managed session을 사용한다. network·408·429·5xx만 최대 두 번 다시
시도하며 250ms·750ms 지연과 invocation 기반 jitter를 적용한다. 2초 이하의 `Retry-After`만
남은 deadline 안에서 사용한다. 400·401·403과 safety block은 다시 시도하지 않는다.

final event의 JSON parse 또는 `AgentTaskResult` Schema 검증이 실패하면 이전 응답 text·digest와
최대 50개 validator error를 넣은 repair Task를 새 session에서 한 번만 실행한다. echo·protocol·
권한 오류는 repair하지 않으며 두 번째 Schema 실패 뒤에는 세 번째 생성을 호출하지 않는다.
모든 HTTP timeout은 Task의 남은 deadline보다 길 수 없고, deadline 뒤 결과는 반환하지 않는다.

동기 Agent 호출의 session 삭제가 실패하면 API는 `AGENT_SESSION_CLEANUP` Outbox를 남긴다.
private Worker의 `POST /internal/v1/agent-sessions:cleanup`은 이 항목만 별도로 lease하고, 설정에
고정된 서울 Runtime resource에서 session을 삭제한다. 404는 이미 삭제된 것으로 간주한다.
transport 오류는 최대 다섯 번까지 지수형 지연으로 다시 시도하고, 잘못된 payload·resource scope
또는 재시도 소진은 원문 오류를 저장하지 않은 안정적인 failure code와 함께 `DEAD_LETTER`로
보낸다. 운영 환경에서는 Scheduler가 IAM 인증으로 이 내부 endpoint를 주기적으로 호출해야 한다.

private Worker의 `GET /internal/v1/dead-letters`는 payload 원문 없이 outbox id, topic, aggregate id,
시도 횟수, failure code, 시각과 digest만 반환한다. 재처리는
`POST /internal/v1/dead-letters/{outbox_id}:reprocess`에서 현재 failure code를 다시 잠그고,
허용된 일시 장애 코드에만 수행한다. 현재 허용값은 외부 Runtime 복구 뒤 다시 시도할 수 있는
`AGENT_CLEANUP_RETRY_EXHAUSTED`뿐이다. payload·scope 오류는 자동 재처리하지 않는다.

재처리 명령에는 고유 request id, 정형 remediation code와 `PR-`, `INC-`, `CHG-` change reference가
필수다. 원문 운영 메모나 비밀값은 받지 않는다. 명령과 이전 failure·attempt는 별도 audit row에
원자적으로 남기고 Outbox를 `PENDING`으로 되돌린다. 이 내부 API는 Cloud Run IAM 인증을 통과한
운영 identity에만 호출 권한을 부여해야 한다.

## Evidence 갱신

connector 감시 작업은 원본에서 확인한 revision과 관측 시각을
`POST /internal/v1/evidence:refresh`에 전달한다. Control API는 저장된 원본 checksum 또는 문서
version과 실제로 달라진 경우에만 해당 Evidence를 `STALE`로 표시한다. API 자료는 1일, dataset은
30일, web·PDF는 90일의 기본 정책으로 만료를 평가하며, 기준일이 없으면 최신으로 간주하지 않는다.

영향받은 current Result에는 `invalidation_reason_codes`가 추가되고 `freshness`가 `STALE`이 된다.
동시에 `EVIDENCE_RETRIEVAL`부터의 선택적 Workflow를 원자적으로 생성한다. 새 Snapshot이 검증되어
커밋되면 새 Evidence는 `ACTIVE`, 같은 원본의 이전 Evidence는 `SUPERSEDED`, 검증된 상충 자료는
`CONFLICT`가 된다. 재계산이 이미 실행 중이면 새 Workflow를 중첩 생성하지 않고 `409`로 거절한다.

## 공식 창업 준비 절차

후보를 선택한 뒤
`GET /v1/projects/{project_id}/candidate-selections/{selection_id}/preparation-guide`로 현재
행정구역의 공식 준비 절차를 조회한다. Control API는 `get_official_procedure`를 사용해 사업자등록,
식품접객업 영업신고, 시설 기준, 위생교육, 옥외광고물과 소방 확인을 각각 조회한다. 응답에는 절차별
관할 기관, 기준일, Evidence와 공식 source trace가 포함된다.

일부 공식 자료가 없거나 오래됐거나 MCP 호출이 실패해도 빈 정상 안내로 바꾸지 않는다. 해당 절차를
`ERROR`, `NOT_FOUND`, `STALE` 또는 `PARTIAL`로 보존하고 전체 안내를 `REVIEW_REQUIRED` 또는
`UNAVAILABLE`로 표시한다. 현재 선택 후보와 확정 행정구역이 없으면 `409`다. 이 API는 안내와 인간
행동 체크만 제공하며 신고, 등록, 계약 또는 결제를 외부 기관에 제출하지 않는다.
