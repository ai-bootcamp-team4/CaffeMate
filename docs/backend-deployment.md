# Backend deployment

`cloudbuild.backend.yaml`은 한 backend image를 빌드해 migration job을 먼저 실행한 뒤
Control API와 Worker 두 Cloud Run service에 같은 digest를 배포한다. 이 파일은 리소스나
IAM을 생성하지 않으며 기존 Secret Manager, Cloud SQL, VPC와 service IAM 설정을 보존한다.

## Trigger contract

- region: `asia-northeast3`
- event: `main` push
- config: `cloudbuild.backend.yaml`
- image tag: immutable `COMMIT_SHA`
- deploy order: image push → migration job update·실행 → API → Worker

기존 frontend trigger와 분리한다. backend 관련 경로가 바뀔 때만 실행하도록 trigger의
included files를 제한할 수 있지만, 정확성 때문에 `api/**`, `worker/**`, `docs/contracts/**`,
`deploy/backend.Dockerfile`, `cloudbuild.backend.yaml`, `api/uv.lock`은 모두 포함해야 한다.

## Pre-provisioned resources

아래 리소스는 trigger를 켜기 전에 관리자가 한 번 생성하고 read-back해야 한다.

1. `caffemate-backend` Docker Artifact Registry repository
2. `caffemate-api`, `caffemate-worker` Cloud Run service
3. `caffemate-migrate` Cloud Run job
4. API·Worker·migration별 runtime service account
5. Cloud SQL, database와 Secret Manager secret
6. `WORKFLOW_STAGE_READY` Pub/Sub topic과 authenticated push subscription
7. outbox drain endpoint를 호출할 authenticated Cloud Scheduler job

API는 browser가 호출하므로 network ingress는 `all`이지만 모든 업무 요청을 Firebase ID
token으로 다시 검증한다. `allUsers` Cloud Run Invoker가 필요하면 관리자가 API service에만
한 번 부여하고 정책을 read-back한다. build는 IAM policy를 수정하지 않는다.

Worker ingress는 `internal`이다. 같은 project의 Pub/Sub subscription과 Cloud Scheduler는
default `run.app` URL로 내부 호출할 수 있다. 각 호출 identity에는 Worker service의
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
- Worker의 `PUBSUB_SUBSCRIPTION`, `WORKFLOW_STAGE_TOPIC_RESOURCE`

Migration job은 API 시작 명령을 사용하지 않고 `caffemate-api migrate`를 한 task, 재시도 0으로
실행한다. 실패하면 API와 Worker 배포 단계로 진행하지 않는다.

## Verification gate

배포 완료 보고 전 운영 source of truth에서 다음을 모두 확인한다.

1. build success와 pushed image digest
2. migration execution success
3. API·Worker latest ready revision이 같은 image digest와 source revision label을 사용
4. API `/healthz`와 Worker `/healthz`가 인증된 요청에서 HTTP 200
5. API 업무 endpoint의 무인증 요청이 거절됨
6. Worker 업무 endpoint가 internet과 권한 없는 identity에서 거절됨
7. test Workflow outbox가 `PUBLISHED`가 되고 Pub/Sub push 뒤 stage event가 이어짐

하나라도 확인하지 못하면 배포 상태는 `pending`이다.
