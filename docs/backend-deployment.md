# Backend deployment

`cloudbuild.backend.yaml`은 한 backend image를 빌드해 migration job을 먼저 실행한 뒤
Control API와 Worker 두 Cloud Run service에 같은 digest를 배포한다. 이 파일은 리소스나
IAM을 생성하지 않으며 기존 Secret Manager, Cloud SQL, VPC와 service IAM 설정을 보존한다.

## Trigger contract

- region: `asia-northeast3`
- event: `main` push
- config: `cloudbuild.main-webhook.yaml`
- image tag: immutable Cloud Build `BUILD_ID`, with the full cloned Git commit
  recorded in every Cloud Run source revision label
- deploy order: image push → migration job update·실행 → API → Worker

현재 GitHub 연결은 하나의 검증된 webhook을 사용하므로 frontend와 backend를 같은 trigger에서
배포한다. 다만 `scripts/resolve-main-deploy-scope.sh`가 병합 커밋의 변경 경로를 확인하여 backend
image 입력이 바뀐 경우에만 migration, API와 Worker 배포를 실행한다. frontend만 바뀌면 backend
단계는 성공 상태로 즉시 건너뛴다. backend 배포가 선택된 경우에는 migration과 service 배포
순서를 그대로 지킨다.
`cloudbuild.backend.yaml`은 연결형 GitHub trigger를 도입할 때 사용할 수 있는 별도 설정으로
유지한다.

## Pre-provisioned resources

아래 리소스는 trigger를 켜기 전에 관리자가 한 번 생성하고 read-back해야 한다.

1. `caffemate-backend` Docker Artifact Registry repository
2. `caffemate-api`, `caffemate-worker` Cloud Run service
3. `caffemate-migrate` Cloud Run job
4. API·Worker·migration별 runtime service account
5. Cloud SQL, database와 Secret Manager secret
6. Agent Runtime session cleanup endpoint를 호출할 authenticated Cloud Scheduler job

첫 번째 기반 리소스 묶음은 저장소의 idempotent bootstrap으로 생성한다. 이 명령은
`caffemate-*` 이름의 Artifact Registry, 전용 service account와 Secret Manager 항목만
생성하며 Cloud SQL이나 Cloud Run service는 만들지 않는다. Secret 값은 표준 출력이나
저장소 파일에 기록하지 않는다.

```bash
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
  ./scripts/bootstrap-backend-foundation.sh
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
  ./scripts/verify-backend-foundation.sh
```

bootstrap이 성공했다는 메시지만으로 생성 완료를 판단하지 않는다. 별도 verifier의 모든
read-back 항목이 `PASS`여야 이 기반 리소스 묶음을 준비된 상태로 취급한다.

Cloud SQL은 두 번째 bootstrap으로 생성한다. 기본 구성은 서울 리전의 PostgreSQL 16,
`db-g1-small`, zonal availability, 10GB SSD 자동 증가, 자동 backup과 point-in-time recovery다.
개발 속도를 위한 작은 tier이지만 backup과 삭제 보호는 생략하지 않는다. Cloud SQL Python
Connector가 public IP로 인증·암호화 연결하며 authorized network는 한 건도 열지 않는다.

```bash
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
  ./scripts/bootstrap-cloud-sql.sh
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
  ./scripts/verify-cloud-sql.sh
```

운영 트래픽이나 성능 시험을 시작하기 전에는 tier와 zonal availability를 다시 검토해야 한다.
현재 구성을 고가용성 운영 준비 완료로 표현하지 않는다.

Backend image와 migration job은 full commit SHA를 release identity로 사용한다. 배포 스크립트는
image가 없을 때 전용 build identity로 먼저 빌드하고, migration job에 Cloud SQL connection과
Secret Manager password를 주입한다. migration 실행 뒤 별도 `verify-migrations` 실행이 저장소의
모든 migration 이름과 checksum을 데이터베이스에서 다시 확인한다.

```bash
revision=$(git rev-parse HEAD)
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
  ./scripts/deploy-migration-job.sh
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
  ./scripts/verify-migration-job.sh
```

Cloud Run job 실행 성공만으로 migration 적용을 증명하지 않는다. 두 번째 검증 실행과 job의
digest-pinned image·source revision 설정을 모두 read-back해야 한다.

API와 Worker runtime은 migration이 검증된 같은 digest를 사용한다. API만 Cloud Run IAM에서
공개 호출을 허용하고 모든 업무 endpoint는 Firebase ID token을 다시 검사한다. Worker는 internal
ingress를 유지하며 Scheduler와 Worker identity만 invoker다. 사용자 제안은 Control API가 한
transaction에서 동기 실행하므로 Worker, Pub/Sub 또는 Outbox를 거치지 않는다.

Agent Runtime release는 임의의 로컬 작업 트리를 배포하지 않는다. 요청한 full commit SHA가
깨끗한 현재 checkout 및 `origin/main`과 일치해야 한다. Cloud Build는 로컬 업로드를 빌드하지
않고 GitHub의 해당 commit을 직접 checkout한다. build-only 단계가 source SHA, build id와 digest를
만들고, 별도 승인 단계가 같은 digest에 `approved-<SHA>` tag를 붙인다. release 단계는 이 승인
산출물만 소비하므로 새 digest를 저장소 manifest에 다시 commit하는 순환이 없다. 갱신 뒤에는
Runtime resource, container digest, class method, source·build label과 effective identity를 다시
읽는다. 고정 Runtime이 없으면 release script가 임의의 새 resource를 만들지 않는다. 먼저
bootstrap으로 생성된 resource id를 인간이 manifest에 승인한 뒤 release를 실행한다.

MCP와 Agent release-preflight 이미지도 `cloudbuild.mcp-image.yaml`에 전용
`caffemate-backend-build` identity를 고정한다. 호출자가 `--service-account`를 빠뜨려도 기본 Compute
identity로 provenance가 갈라지지 않게 하기 위함이다. SHA tag는 불변이므로 잘못된 identity가 먼저
태그를 차지한 뒤 재빌드로 덮어쓰는 복구 방식을 허용하지 않는다. verifier는 image digest뿐 아니라
build identity, exact Git checkout step과 source revision을 모두 대조한다.

Agent GCP preflight 이미지는 로컬 소스를 업로드하는 일반 `gcloud builds submit`으로 만들지 않는다.
아래 build-only 스크립트만 사용한다. 이 스크립트는 깨끗한 `origin/main` SHA를 확인한 뒤
`--no-source`로 Cloud Build를 호출하므로, Cloud Build가 GitHub의 검토된 SHA를 직접 checkout한다.
같은 SHA tag가 일부만 존재하거나 trusted provenance 없이 이미 존재하면 덮어쓰지 않고 새 source
revision을 요구한다. 이 단계는 MCP 서비스를 배포하지 않는다.

```bash
revision=$(git rev-parse HEAD)
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
  ./scripts/build-agent-gcp-preflight.sh
```

```bash
revision=$(git rev-parse HEAD)
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
  ./scripts/build-agent-runtime-release.sh
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
  ./scripts/approve-agent-runtime-release.sh
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
  ./scripts/deploy-agent-runtime.sh
```

```bash
revision=$(git rev-parse HEAD)
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
CAFFEMATE_AGENT_RUNTIME_RESOURCE_ID='<numeric-reasoning-engine-id>' \
CAFFEMATE_DOCUMENT_BUCKET='proj-aj20-211200020328-caffemate-documents' \
  ./scripts/deploy-api-worker-runtime.sh
CAFFEMATE_GCP_PROJECT_ID=proj-aj20-211200020328 \
CAFFEMATE_SOURCE_REVISION="$revision" \
CAFFEMATE_DOCUMENT_BUCKET='proj-aj20-211200020328-caffemate-documents' \
  ./scripts/verify-api-worker-runtime.sh
```

API는 browser가 호출하므로 network ingress는 `all`이지만 모든 업무 요청을 Firebase ID
token으로 다시 검증한다. `allUsers` Cloud Run Invoker가 필요하면 관리자가 API service에만
한 번 부여하고 정책을 read-back한다. build는 IAM policy를 수정하지 않는다.

배포 스크립트는 `CAFFEMATE_AGENT_RUNTIME_RESOURCE_ID`가 가리키는 서울 Runtime을 먼저
조회하고, API service account에 해당 Runtime resource 범위의 `roles/aiplatform.user`를
부여하지 않는다. Control API에는 고정 Runtime의 `query`만 포함하는 custom role을 resource
범위로 부여한다. Runtime 관리형 identity에는 GCP가 관리하는 비변경성 기본 실행 권한과 고정
Runtime 범위의 session 수명주기 권한만 남긴다. `agentContextEditor`, `expressUser`와 직접
프로젝트 권한은 제거한다. MCP identity도 RAG query와 rerank만 허용한다. 검증 스크립트는
custom role의 실제 permission 목록과 broad predefined role 제거를 읽어 확인한다. 이어 실제
API와 MCP 실행 계정으로 일회성 Cloud Run Job을 실행하여, 고정 리소스의 허용 권한과 금지된
변경 권한을 `testIamPermissions` 응답에서 함께 확인한다.

검증 스크립트는 API image와 같은 service account를 쓰는 일회성
Cloud Run Job으로 한 ephemeral stream 안의 실제 session 생성, Agent 실행, typed final event 검증과 session 삭제를
모두 통과시킨다. 이어 producer와 같은 Agent GCP preflight를 별도 최소 권한 verifier identity로
실행해 RAG corpus/file, embedding, retrieval, reranker, generation model과 pinned Runtime을 함께
검사한다. 단순 resource 조회는 실행 가능성의 증거로 취급하지 않는다.

Worker ingress는 `internal`이다. 같은 project의 Cloud Scheduler는 default `run.app` URL로
Agent session cleanup을 호출한다. Scheduler identity에는 Worker service의
`roles/run.invoker`만 부여한다. Worker를 public invoker로 열지 않는다.

## Preserved runtime configuration

Cloud Build는 image, 실행 command, service account, ingress, labels만 갱신한다. 다음 값은
bootstrap 시 기존 service와 migration job에 설정하고 배포 전후 read-back한다.

- Cloud SQL connection 또는 private network
- `DB_USER`, `DB_NAME`, `INSTANCE_CONNECTION_NAME`, `CLOUD_SQL_IP_TYPE`
- Secret Manager 기반 `DB_PASS`
- API의 `FIREBASE_PROJECT_ID`, `CAFFEMATE_POLICY_SNAPSHOT_ID`,
  `WORKER_SERVICE_ACCOUNT_EMAIL`, `CONTROL_API_AUDIENCE`, `AGENT_RUNTIME_PROJECT_ID`,
  `AGENT_RUNTIME_RESOURCE_ID`
- API의 Secret Manager 기반 `AGENT_RUNTIME_USER_HMAC_SECRET`
- API의 `MCP_BASE_URL`, `MCP_AUDIENCE`
- API와 MCP에만 주입하는 Secret Manager 기반 `MCP_SCOPE_HMAC_SECRET`
- Worker의 `WORKER_ID`, `AGENT_RUNTIME_PROJECT_ID`, `AGENT_RUNTIME_RESOURCE_ID`
- API·Worker의 regional `DOCUMENT_BUCKET`과 API의
  `DOCUMENT_SIGNING_SERVICE_ACCOUNT_EMAIL`

Migration job은 API 시작 명령을 사용하지 않고 `caffemate-api migrate`를 한 task, 재시도 0으로
실행한다. 실패하면 API와 Worker 배포 단계로 진행하지 않는다.

## Verification gate

배포 완료 보고 전 운영 source of truth에서 다음을 모두 확인한다.

1. build success와 pushed image digest
2. migration execution success
3. API·Worker latest ready revision이 같은 image digest와 source revision label을 사용
4. API `/health`가 외부 요청에서 HTTP 200이고 Worker `/health`가 외부 요청을 거절함
5. API 업무 endpoint의 무인증 요청이 거절됨
6. Worker 업무 endpoint가 internet과 권한 없는 identity에서 거절됨
7. 단일 `RUN_PROPOSAL` 실행이 `SUCCEEDED` 결과를 만들고 Agent session cleanup Scheduler가
   Worker 내부 endpoint에서 HTTP 200을 반환함
8. Control API identity로 Agent Runtime ephemeral stream의 session 생성·실행·typed final 검증·삭제 성공
9. regional document bucket의 public access prevention·CORS·최소 권한 IAM read-back과,
   signed upload → object 검증 → scan 결과 → parser 결과 → 실제 `DOCUMENT_EXTRACT` Agent →
   extraction form → signed download canary 성공

하나라도 확인하지 못하면 배포 상태는 `pending`이다.

Cloud Run의 Google Frontend는 정확한 `/healthz` 경로를 예약 경로로 처리하므로 backend
liveness에는 `/health`를 사용한다. `/healthz`의 Google Frontend 404는 애플리케이션 상태를
증명하지 못하며 운영 검증에 사용하지 않는다.
