# Frontend deployment

This repository deploys only the Vite `dist` output. Nginx listens on the
Cloud Run `PORT`, serves `/_healthz` without application logic, and falls back
to `index.html` for client-side routes. No backend process is included.

## Cloud Build trigger contract

Configure the GitHub repository connection and trigger in
`asia-northeast3`. The trigger must use:

- event: push to branch
- branch expression: `^main$`
- build configuration: `cloudbuild.yaml`
- build service account: a dedicated least-privilege account

The checked-in defaults can be overridden by trigger substitutions:

| Substitution | Default | Purpose |
| --- | --- | --- |
| `_REGION` | `asia-northeast3` | Artifact Registry and Cloud Run region |
| `_AR_REPOSITORY` | `caffemate-web` | Existing Docker repository |
| `_IMAGE_NAME` | `web` | Container image name |
| `_SERVICE_NAME` | `caffemate-web` | Cloud Run service name |
| `_RUNTIME_SERVICE_ACCOUNT` | `caffemate-web-runtime@${PROJECT_ID}.iam.gserviceaccount.com` | Existing frontend runtime identity |

`PROJECT_ID`, `COMMIT_SHA`, and `SHORT_SHA` are Cloud Build built-in
substitutions. Images use the immutable commit SHA instead of `latest`.

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
