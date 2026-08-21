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
