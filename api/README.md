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
Stage가 포함된다. confirm·cancel은 별도 command이며 preview 생성으로 대신 처리하지 않는다.
