# Main deployment

The main webhook trigger uses `cloudbuild.main-webhook.yaml` to deploy the Vite
frontend and the FastAPI/Worker backend from the same cloned `main` revision.
The trigger first compares the merged commit with its parent and deploys only
the runtime whose image inputs changed. Web-only changes build and deploy only
the Web service. Backend changes build one shared backend image, run migrations,
and update the API and Worker in order. Documentation-only changes finish as a
no-op deployment. This avoids rebuilding the entire stack for a copy-only UI
change while preserving the backend migration boundary.

The frontend image still contains only the Vite `dist` output. Nginx listens
on the Cloud Run `PORT`, serves `/_healthz` without application logic, and
falls back to `index.html` for client-side routes.

## Cloud Build trigger contract

Configure the GitHub repository connection and trigger in
`asia-northeast3`. The trigger must use:

- event: push to branch
- branch expression: `^main$`
- inline build configuration: `cloudbuild.main-webhook.yaml`
- build service account: `caffemate-backend-build`, with explicit
  `serviceAccountUser` access to the web, API, Worker, and migration identities

The checked-in defaults can be overridden by trigger substitutions:

| Substitution | Default | Purpose |
| --- | --- | --- |
| `_REGION` | `asia-northeast3` | Artifact Registry and Cloud Run region |
| `_WEB_REPOSITORY` | `caffemate-web` | Existing frontend Docker repository |
| `_BACKEND_REPOSITORY` | `caffemate-backend` | Existing backend Docker repository |
| `_WEB_SERVICE` | `caffemate-web` | Frontend Cloud Run service |
| `_API_SERVICE` | `caffemate-api` | Control API Cloud Run service |
| `_WORKER_SERVICE` | `caffemate-worker` | Worker Cloud Run service |
| `_MIGRATION_JOB` | `caffemate-migrate` | Migration Cloud Run job |
| `_DEPLOY_SCOPE` | `auto` | `auto`, `web`, `backend`, `all`, or `none` deployment scope |

`PROJECT_ID` and `BUILD_ID` are Cloud Build built-in substitutions. Images use
the immutable build id instead of `latest`; each deployed resource records the
full commit that last changed its runtime inputs. Therefore Web and Backend
source revision labels may intentionally differ after a path-scoped deployment.

The checked-in path classifier is `scripts/resolve-main-deploy-scope.sh`.
Changes under `src`, `public`, the frontend build files, or Nginx configuration
select Web. Changes under `api`, `worker`, backend image inputs, runtime fixtures,
or shared contracts select Backend. Test-only files and documentation outside
the runtime contract are excluded. A manual recovery build may override
`_DEPLOY_SCOPE`; normal GitHub pushes keep `auto`.

## Required Google Cloud resources

Before enabling the trigger, read back and confirm:

1. Cloud Build, Artifact Registry, Cloud Run, and Resource Manager APIs.
2. A Docker Artifact Registry repository in `_REGION`.
3. A frontend runtime service account with no backend permissions.
4. A dedicated build service account with only the permissions needed to
   write the repository image, deploy the Cloud Run service, write build
   logs, and act as the runtime service account.
5. A second-generation GitHub connection, linked repository, and trigger in
   the same region.

Public frontend access was explicitly approved for this service. Bootstrap the
service once with an authorized administrator, grant `allUsers` the Cloud Run
Invoker role, and verify that policy by reading it back. Trigger builds update
the existing service without changing its IAM policy; this lets the dedicated
build account use the narrower Cloud Run Developer role instead of Cloud Run
Admin. Do not reuse the bootstrap command or public IAM policy for private
services.

## Local validation

Run the scaffold checks before the frontend exists:

```sh
./scripts/validate-deploy-scaffold.sh
```

After `package.json`, `package-lock.json`, and the frontend sources are ready,
run a complete local image build:

```sh
FULL_DOCKER_BUILD=1 ./scripts/validate-deploy-scaffold.sh
```

The complete check builds `caffemate-web:local`; it does not push an image,
create cloud resources, or deploy a service.

After an authorized trigger deployment, verify the successful build, pushed
image digest, ready Cloud Run revision, source revision label, `/healthz`, and
the SPA entry URL. Do not report the deployment as complete until both HTTP
checks return `200` from the deployed URL.

The Cloud Run Google Frontend intercepts the exact `/healthz` path for this
service and returns its own `404` before the request reaches Nginx. Use
`/_healthz` for external and container health verification. This behavior was
confirmed against the deployed service logs; `/healthz` is not an application
readiness signal for this deployment.
