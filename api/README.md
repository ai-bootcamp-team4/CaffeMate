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
