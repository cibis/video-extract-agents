# GitLab Pipeline & SDLC Guide

This document explains the CI/CD pipeline in detail — every stage, every job, when Azure environments are created and destroyed, and how GitLab should be used throughout the software development lifecycle of this project.

---

## Table of Contents

- [Pipeline at a Glance](#pipeline-at-a-glance)
- [Global Variables](#global-variables)
- [Reusable Templates (YAML Anchors)](#reusable-templates-yaml-anchors)
  - [`&docker-login`](#docker-login)
  - [`&azure-login`](#azure-login)
  - [`&terraform-setup`](#terraform-setup)
- [Stage-by-Stage Breakdown](#stage-by-stage-breakdown)
  - [Stage 1 — `lint`](#stage-1-lint)
  - [Stage 2 — `build_images`](#stage-2-build_images)
  - [Stage 3 — `aca_test_env_create`](#stage-3-aca_test_env_create)
  - [Stage 4 — `deploy_test_services`](#stage-4-deploy_test_services)
  - [Stage 5 — `e2e_tests`](#stage-5-e2e_tests)
  - [Stage 6 — `collect_e2e_logs`](#stage-6-collect_e2e_logs)
  - [How to download the logs after a pipeline run](#how-to-download-the-logs-after-a-pipeline-run)
  - [Using the logs to diagnose failures](#using-the-logs-to-diagnose-failures)
  - [Stage 7 — `aca_test_env_destroy`](#stage-7-aca_test_env_destroy)
  - [Stage 8 — `push_to_acr`](#stage-8-push_to_acr)
  - [Stage 9 — `deploy_dev`](#stage-9-deploy_dev)
  - [Stage 10 — `manual_approval`](#stage-10-manual_approval)
  - [Stage 11 — `deploy_prod`](#stage-11-deploy_prod)
- [Azure Environments — Lifecycle Summary](#azure-environments-lifecycle-summary)
- [GitLab CI Variables Required](#gitlab-ci-variables-required)
- [SDLC Workflow](#sdlc-workflow)
  - [Branch Strategy](#branch-strategy)
  - [Day-to-Day Development Flow](#day-to-day-development-flow)
  - [Merge Requests](#merge-requests)
  - [Environments in GitLab](#environments-in-gitlab)
  - [Rolling Back a Production Deployment](#rolling-back-a-production-deployment)
  - [Infrastructure Changes (Terraform)](#infrastructure-changes-terraform)
  - [Adding a New Service](#adding-a-new-service)
  - [Handling Secrets Rotation](#handling-secrets-rotation)
  - [Pipeline Failure Triage](#pipeline-failure-triage)
- [Completing a Release — Step-by-Step](#completing-a-release-step-by-step)
  - [Prerequisites (one-time setup)](#prerequisites-one-time-setup)
  - [Step 1 — Merge to `main`](#step-1-merge-to-main)
  - [Step 2 — Verify `push_to_acr`](#step-2-verify-push_to_acr)
  - [Step 3 — Monitor `deploy_dev`](#step-3-monitor-deploy_dev)
  - [Step 4 — Smoke test dev](#step-4-smoke-test-dev)
  - [Quick-reference: full release command sequence](#quick-reference-full-release-command-sequence)

---

## Pipeline at a Glance

```
push to any branch
        │
        ▼
┌─────────────┐
│    lint      │  ESLint (Node.js) + Ruff (Python) — path-filtered
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  build_images   │  Docker build + push to ACR (7 images, tagged with commit SHA)
└────────┬────────┘
         │
         ▼
┌──────────────────────┐
│  aca_test_env_create │  Terraform creates ephemeral Azure environment
└──────────┬───────────┘   video-extract-test-{pipeline_id}
           │
           ▼
┌─────────────────────┐
│ deploy_test_services │  az containerapp update — push images to test ACA
└──────────┬──────────┘
           │
           ▼
┌───────────┐
│ e2e_tests │  pytest tests/e2e/ against live Azure test environment
└─────┬─────┘
      │
      ▼ (always — even on failure)
┌──────────────────────┐
│  collect_e2e_logs    │  az containerapp logs → ci-logs/ artifact
└──────────┬───────────┘
           │
           ▼ (always — even on failure)
┌───────────────────────┐
│ aca_test_env_destroy  │  Terraform destroys the ephemeral environment
└──────────┬────────────┘
           │
           │   (main branch only from here)
           ▼
┌─────────────┐
│ push_to_acr │  Re-tag images with :latest in ACR
└──────┬──────┘
       │
       ▼
┌────────────┐
│ deploy_dev │  az containerapp update → video-extract-dev
└──────┬─────┘
       │
       ▼
┌──────────────────┐
│ manual_approval  │  Human gate — someone clicks "Run" in GitLab UI
└────────┬─────────┘
         │
         ▼
┌─────────────┐
│ deploy_prod │  placeholder — production deployment not yet configured
└─────────────┘
```

---

## Global Variables

Defined at the top of `.gitlab-ci.yml` and available to all jobs:

| Variable | Value | Purpose |
|---|---|---|
| `DOCKER_DRIVER` | `overlay2` | Docker storage driver for DinD |
| `DOCKER_TLS_CERTDIR` | `/certs` | TLS cert path for Docker-in-Docker |
| `ACR_REGISTRY` | `${ACR_REGISTRY}` | Container registry hostname (set as GitLab CI var) |
| `IMAGE_TAG` | `${CI_COMMIT_SHORT_SHA}` | Every image is tagged with the 8-char git commit SHA |
| `TF_DIR` | `infrastructure/terraform/envs/test` | Path Terraform jobs `cd` into |
| `COMPOSE_FILE` | `infrastructure/docker-compose/docker-compose.yml` | Local compose file reference |

`IMAGE_TAG` being the commit SHA is the critical traceability link — every image, every deployment, every test run is pinned to an exact commit.

---

## Reusable Templates (YAML Anchors)

The pipeline defines three YAML anchors used as `before_script` blocks across multiple jobs.

### `&docker-login`
```yaml
before_script:
  - echo "$ACR_PASSWORD" | docker login "$ACR_REGISTRY" -u "$ACR_USERNAME" --password-stdin
```
Used by all `build_images` jobs and `push_to_acr`. Authenticates Docker CLI to Azure Container Registry using admin credentials stored as masked GitLab CI variables.

### `&azure-login`
```yaml
before_script:
  - az login --service-principal -u "$AZURE_CLIENT_ID" -p "$AZURE_CLIENT_SECRET" --tenant "$AZURE_TENANT_ID"
  - az account set --subscription "$AZURE_SUBSCRIPTION_ID"
```
Used by `deploy_test_services`, `deploy_dev`, and `deploy_prod`. Authenticates the Azure CLI using the Terraform service principal.

### `&terraform-setup`
```yaml
image: hashicorp/terraform:1.9
before_script:
  - cd "$TF_DIR"                                          # cd to test env terraform dir
  - export TF_VAR_pipeline_id="$CI_PIPELINE_ID"          # unique ID for ephemeral resource naming
  - export TF_VAR_image_tag="$IMAGE_TAG"                  # commit SHA for container image
  - export TF_VAR_acr_login_server="$ACR_REGISTRY"
  - export TF_VAR_acr_username="$ACR_USERNAME"
  - export TF_VAR_acr_password="$ACR_PASSWORD"
  - export TF_VAR_db_admin_password="$DB_ADMIN_PASSWORD"
  - export TF_VAR_anthropic_api_key="$ANTHROPIC_API_KEY"
  - export ARM_CLIENT_ID / ARM_CLIENT_SECRET / ARM_TENANT_ID / ARM_SUBSCRIPTION_ID
  - terraform init -backend-config="storage_account_name=$TF_STATE_STORAGE_ACCOUNT"
```
Used by `aca_test_env_create` and `aca_test_env_destroy`. Maps GitLab CI variables to Terraform variables and initialises the remote backend.

---

## Stage-by-Stage Breakdown

---

### Stage 1 — `lint`

Two jobs run in parallel, each only triggered when their relevant files change.

#### `lint:api-gateway`
- **Image:** `node:20-alpine`
- **Trigger:** any change under `backend/api-gateway/**`
- **What it does:** runs `npm ci` then `npm run lint` (ESLint)
- **Fails the pipeline if:** linting errors are found in the Node.js codebase

#### `lint:python`
- **Image:** `python:3.11-slim`
- **Trigger:** any change to `**/*.py` under `backend/` or `mcp-servers/`
- **What it does:** installs `ruff` and runs `ruff check` across all Python services
- **Fails the pipeline if:** any Python file has a linting error

**Path filtering** means a frontend-only commit skips both lint jobs entirely, and an api-gateway change only triggers `lint:api-gateway`.

---

### Stage 2 — `build_images`

Eight jobs, all using the same `&build-image` template. They run in parallel.

**Template:** `docker:26` image with `docker:26-dind` service (Docker-in-Docker).

Each job follows the same pattern:
```bash
docker build -t "$ACR_REGISTRY/<service>:$IMAGE_TAG" <service-dir>/
docker push "$ACR_REGISTRY/<service>:$IMAGE_TAG"
```

| Job | Source directory | Image name |
|---|---|---|
| `build:api-gateway` | `backend/api-gateway/` | `api-gateway` |
| `build:agent-orchestrator` | `backend/agent-orchestrator/` | `agent-orchestrator` |
| `build:preprocessing-worker` | `backend/preprocessing-worker/` | `preprocessing-worker` |
| `build:mcp-server-analysis` | `mcp-servers/mcp-server-analysis/` | `mcp-server-analysis` |
| `build:mcp-server-processing` | `mcp-servers/mcp-server-processing/` | `mcp-server-processing` |
| `build:angular-shell` | `frontend/angular-shell/` | `angular-shell` |
| `build:librechat` | `frontend/librechat/` | `librechat` |

After this stage, all 7 images are in ACR tagged with the commit SHA. They are not yet tagged `:latest`.

> **Note on the ACR used here:** These images are pushed to the production ACR (`videoextractdevacr` or `videoextractprodacr`). The test environment pulls from the same registry — there is no separate test registry.

---

### Stage 3 — `aca_test_env_create`

**Azure resources created here.**

- **Image:** `hashicorp/terraform:1.9`
- **Uses:** `&terraform-setup` template
- **Working directory:** `infrastructure/terraform/envs/test/`

```bash
terraform apply -auto-approve
terraform output -json > /tmp/tf-outputs.json
```

#### What Terraform creates in Azure

A full isolated environment named `video-extract-test-{CI_PIPELINE_ID}`:

| Resource | Name |
|---|---|
| Resource Group | `video-extract-test-{pipeline_id}` |
| Storage Account | `videoextracttest{pipeline_id}<suffix>` |
| Storage Container | `videos` |
| PostgreSQL (ACA container, Azure Files) | `postgresql` container app inside the ACA environment |
| Service Bus Namespace | `ve-test-{pipeline_id}-sb` |
| Service Bus Queues | `video-uploaded`, `video-indexed`, `job-queued` |
| Container Registry | `videoextracttest{pipeline_id}acr` |
| ACA Environment | `videoextract-test-{pipeline_id}-cae` |
| Log Analytics Workspace | `videoextract-test-{pipeline_id}-law` |
| Container Apps (8) | `postgresql`, `api-gateway`, `agent-orchestrator`, `preprocessing-worker`, `mcp-server-analysis`, `mcp-server-processing`, `angular-shell`, `librechat` |

Resources **not** created in the test environment:
- Application Insights (too expensive; `appinsights_connection_string = ""`)
- Azure Front Door (too slow to provision per run; `front_door_url = ""`)
- Azure Key Vault (secrets injected directly as env vars via `TF_VAR_*`)

#### Artifacts

`terraform output -json` is saved as a pipeline artifact (`/tmp/tf-outputs.json`, expires in 2 hours). The next stage reads `api_gateway_fqdn` from this file to know where to point the tests.

---

### Stage 4 — `deploy_test_services`

- **Image:** `mcr.microsoft.com/azure-cli:latest`
- **Uses:** `&azure-login` template

```bash
SERVICES=(api-gateway agent-orchestrator preprocessing-worker
          mcp-server-analysis mcp-server-processing angular-shell librechat)
RG="video-extract-test-${CI_PIPELINE_ID}"

for svc in "${SERVICES[@]}"; do
  az containerapp update \
    --name "$svc" \
    --resource-group "$RG" \
    --image "$ACR_REGISTRY/$svc:$IMAGE_TAG" || true
done
```

Terraform already created the container apps with a default image. This stage updates each one to the freshly built commit-SHA-tagged image. The `|| true` prevents a single slow or already-running update from blocking the loop.

---

### Stage 5 — `e2e_tests`

- **Image:** `python:3.11-slim`

```bash
pip install pytest httpx asyncpg
pytest tests/e2e/ -v --tb=short || true
```

The `API_GATEWAY_URL` is derived from the Terraform output artifact:
```yaml
API_GATEWAY_URL: "https://api-gateway.$(cat /tmp/tf-outputs.json | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['api_gateway_fqdn']['value'])")"
```

#### What the E2E tests cover

All tests in `tests/e2e/` are run. They exercise the full pipeline end-to-end against live Azure services:

| Test file | What it tests |
|---|---|
| `test_detect_motion.py` | Upload video → trigger preprocessing → call `detect_motion` MCP tool |
| `test_detect_motion_sports.py` | Same with sports-tuned motion detection tool |
| `test_detect_objects.py` | YOLO object detection tool via full pipeline |
| `test_detect_objects_vision.py` | Frontier vision object detection (frontier; skipped if no API key) |
| `test_analyze_scene.py` | Frontier vision scene description (frontier; skipped if no API key) |
| `test_transcribe_audio.py` | Whisper audio transcription tool |
| `test_estimate_height_above_surface.py` | Height estimation tool |
| `test_followup_job.py` | Follow-up jobs using `parent_job_id` context |

**Frontier tests** (`analyze_scene`, `detect_objects_vision`) automatically skip if `ANTHROPIC_API_KEY` (or the relevant provider credentials) are absent or the provider endpoint is unreachable — they are never hard-failed due to missing keys.

**`|| true` on pytest** — test failures do not block the `aca_test_env_destroy` stage. The environment is always cleaned up. Failures are surfaced in the GitLab UI as a warning on this job.

#### Test flow for each test

```
conftest.py:wipe_test_data    ← DELETE /v1/admin/wipe-test-data (clean slate)
helpers.create_test_session   ← POST /v1/sessions
helpers.upload_video          ← POST /v1/videos → PUT <upload_url>
helpers.wait_for_indexed      ← poll GET /v1/sessions/{id}/assets until indexed
<test submits job>            ← POST /v1/jobs
helpers.wait_for_job          ← poll GET /v1/jobs/{id} until completed/failed
helpers.assert_job_succeeded  ← assert status == 'completed'
helpers.assert_tool_invoked   ← GET /v1/jobs/{id}/logs → assert tool appeared
```

---

### Stage 6 — `collect_e2e_logs`

**Container logs and diagnostics captured here as a downloadable artifact.**

- **Image:** `mcr.microsoft.com/azure-cli:latest`
- **Uses:** `&azure-login` template
- **`when: always`** — runs regardless of whether `e2e_tests` passed or failed

This stage runs immediately after `e2e_tests` and before `aca_test_env_destroy`. It pulls logs from every container in the ephemeral environment while it is still alive, and uploads them as a single GitLab artifact. Once `aca_test_env_destroy` runs, these logs would be gone permanently.

#### What is collected

```
ci-logs/
  api-gateway.log            stdout/stderr, last 2000 lines
  agent-orchestrator.log
  preprocessing-worker.log
  mcp-server-analysis.log
  mcp-server-processing.log
  angular-shell.log
  librechat.log
  postgresql.log
  aca-status.log             provisioning state + replica list for all 8 container apps
  rg-resources.log           full resource group inventory (all Azure resources)
  servicebus.log             queue depths + dead-letter counts for all 3 queues
```

#### How it works

```bash
# Container stdout/stderr (last 2000 lines per service):
az containerapp logs show \
  --name "$svc" \
  --resource-group "$RG" \
  --tail 2000 \
  --output table

# Container app status + replica state (all services):
az containerapp show   --name "$svc" --resource-group "$RG" --query "{...}" --output json
az containerapp replica list --name "$svc" --resource-group "$RG" --output table

# Resource group inventory:
az resource list --resource-group "$RG" --output table

# Service Bus queue depths (active + dead-letter):
az servicebus queue show --namespace-name "$SB_NS" --resource-group "$RG" --name "$q" ...
```

#### Artifact details

| Property | Value |
|---|---|
| Artifact name | `e2e-logs-{CI_PIPELINE_ID}` |
| Expiry | 7 days |
| Triggered | `when: always` — captured on both pass and fail |

---

### How to download the logs after a pipeline run

After any pipeline run that includes the test environment stages:

1. Go to **GitLab → CI/CD → Pipelines**
2. Click the pipeline you want to inspect
3. Find the **`collect_e2e_logs`** job in the pipeline graph — click it to open the job page
4. In the right-hand panel, click **Download artifacts** (or use the **Artifacts** tab at the top of the job page)
5. A `.zip` file named `e2e-logs-{pipeline_id}.zip` downloads — extract it to find the `ci-logs/` directory

Alternatively, download via the GitLab API or `glab` CLI:
```bash
# Using glab CLI (requires glab auth login first):
glab ci artifact <pipeline_id> collect_e2e_logs

# Using the GitLab API directly:
curl --header "PRIVATE-TOKEN: <your-token>" \
  "https://gitlab.com/<group>/<project>/-/jobs/<job_id>/artifacts/download" \
  --output e2e-logs.zip
```

---

### Using the logs to diagnose failures

#### 504 Gateway Timeout / test failure

1. Open `ci-logs/api-gateway.log` — look for OOM signals (`JavaScript heap out of memory`), connection errors to downstream services, or request timeout entries
2. Check `ci-logs/aca-status.log` — look for `provisioningState != Succeeded` or replica counts of 0 on services that should be running
3. Check `ci-logs/agent-orchestrator.log` — look for Python tracebacks or timeout errors calling MCP servers

#### Service Bus stuck / jobs not processing

1. Open `ci-logs/servicebus.log` — look for high `dead` (dead-letter) counts on any queue
2. Check `ci-logs/preprocessing-worker.log` or `ci-logs/agent-orchestrator.log` for the consumer errors that caused dead-lettering

#### Container failed to start

1. Open `ci-logs/aca-status.log` — find the service with a bad provisioning state
2. Open the matching `ci-logs/<service>.log` — startup errors (bad env vars, missing secrets, DB connection failure) will appear at the top

#### Cold start / timeout during warmup

Check `ci-logs/api-gateway.log` around the timestamp when the health poll was running. Look for "Database schema initialised." — if it doesn't appear, the DB was still cold when tests started.

---

### Stage 7 — `aca_test_env_destroy`

**Azure resources destroyed here.**

- **Image:** `hashicorp/terraform:1.9`
- **Uses:** `&terraform-setup` template
- **`when: always`** — this job runs regardless of whether any previous stage failed

```bash
terraform destroy -auto-approve
```

Destroys everything created in `aca_test_env_create` — the entire `video-extract-test-{pipeline_id}` resource group and all resources in it.

This is the safety guarantee: even if `build_images`, `e2e_tests`, or any earlier stage fails or times out, the ephemeral environment is always destroyed. No test environments accumulate in Azure.

---

### Stage 8 — `push_to_acr`


**Only runs on `main` branch.**

- **Image:** `docker:26` with DinD
- **Uses:** `&docker-login` template

```bash
SERVICES=(api-gateway agent-orchestrator ...)
for svc in "${SERVICES[@]}"; do
  docker pull "$ACR_REGISTRY/$svc:$IMAGE_TAG"   # pull the SHA-tagged image
  docker tag  "$ACR_REGISTRY/$svc:$IMAGE_TAG" "$ACR_REGISTRY/$svc:latest"
  docker push "$ACR_REGISTRY/$svc:latest"        # push as :latest
done
```

Images were already pushed to ACR in `build_images` with their commit SHA tag. This stage adds the `:latest` tag to the same images once they have passed all tests on `main`. The `:latest` tag is what dev and prod reference for human-readable "current stable" lookups, but actual deployments use the SHA tag.

---

### Stage 9 — `deploy_dev`

**Only runs on `main` branch.**

- **Image:** `mcr.microsoft.com/azure-cli:latest`
- **Uses:** `&azure-login` template
- **GitLab environment:** `dev`

```bash
for svc in "${SERVICES[@]}"; do
  az containerapp update \
    --name "$svc" \
    --resource-group "video-extract-dev" \
    --image "$ACR_REGISTRY/$svc:$IMAGE_TAG"
done
```

Updates all 7 container apps in the **persistent `video-extract-dev`** resource group to the new image. ACA performs a rolling update — the old revision stays alive until the new one passes its health check.

After all containers are updated, the job starts the `db-init` ACA job and polls until it succeeds. This ensures schema migrations are applied on every deploy, not just on initial provisioning. The pipeline fails if `db-init` reports a failure.

The `environment: name: dev` declaration registers this deployment in GitLab's Environments view, giving a history of what was deployed to dev and when.

---

### Stage 10 — `manual_approval`

**Only runs on `main` branch.**

```yaml
when: manual
allow_failure: false
```

The pipeline pauses here. A team member must go to GitLab → CI/CD → Pipelines → find this pipeline → click the **play button** on `manual_approval` to proceed to production deployment.

`allow_failure: false` means the `deploy_prod` job cannot start until this job is explicitly triggered — it will not time out or auto-proceed.

This is the production gate. The expectation is that someone has:
1. Verified the dev deployment is healthy
2. Smoke-tested the new features on dev
3. Made a conscious decision to release to prod

---

### Stage 11 — `deploy_prod`

**Only runs on `main` branch. Requires `manual_approval` to complete first.**

- **Image:** `alpine:latest`
- **`needs: [manual_approval]`** — explicit job dependency

```bash
echo "Production deployment not yet configured — placeholder"
```

This is a placeholder job. The `video-extract-prod` environment is not currently provisioned or deployed. The stage exists as a gate so the pipeline structure is preserved for future activation.

---

## Azure Environments — Lifecycle Summary

| Environment | Resource Group | Created by | Destroyed by | Lifetime |
|---|---|---|---|---|
| **Ephemeral test** | `video-extract-test-{pipeline_id}` | `aca_test_env_create` | `aca_test_env_destroy` | Single pipeline run (~30–60 min) |
| **Dev** | `video-extract-dev` | Terraform (manual first run) | Manual only | Persistent |
| **Prod** | `video-extract-prod` | Not provisioned | — | Not yet active — `deploy_prod` is a placeholder |

The ephemeral test environment is the key architectural decision — every pipeline run gets a completely fresh, isolated Azure environment. There is no shared test environment that accumulates state or has contention between concurrent pipelines.

---

## GitLab CI Variables Required

All of these must be set in GitLab → Settings → CI/CD → Variables as **masked** variables.

| Variable | Used by | Description |
|---|---|---|
| `ACR_REGISTRY` | `build_images`, `push_to_acr`, deploy stages | ACR login server e.g. `videoextractdevacr.azurecr.io` |
| `ACR_USERNAME` | `build_images`, `push_to_acr` | ACR admin username |
| `ACR_PASSWORD` | `build_images`, `push_to_acr`, Terraform | ACR admin password |
| `AZURE_CLIENT_ID` | `azure-login` template, Terraform | Service principal app ID |
| `AZURE_CLIENT_SECRET` | `azure-login` template, Terraform | Service principal password |
| `AZURE_TENANT_ID` | `azure-login` template, Terraform | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | `azure-login` template, Terraform | Azure subscription ID |
| `DB_ADMIN_PASSWORD` | Terraform | PostgreSQL admin password for test env |
| `ANTHROPIC_API_KEY` | Terraform → container env | Anthropic API key |
| `TF_STATE_ACCESS_KEY` | Terraform backend init | Access key for the `tfstatevideoextract` storage account (passed as `terraform init -backend-config="access_key=..."`) |

Optional (only if using Bedrock or OpenAI):

| Variable | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS credentials for Bedrock |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials for Bedrock |
| `OPENAI_API_KEY` | OpenAI API key |

---

## SDLC Workflow

This section describes how GitLab should be used day-to-day across the full software development lifecycle.

---

### Branch Strategy

```
main
  └── protected, requires MR, CI must pass
feature/your-feature-name
  └── branch from main, short-lived
fix/your-bug-description
  └── branch from main, short-lived
```

**`main` is the only long-lived branch.** Feature and fix branches are created from `main`, worked on, and merged back via Merge Request. There are no `develop`, `staging`, or `release` branches — the pipeline itself provides the environment progression.

**Branch naming convention:**
- `feature/<short-description>` — new functionality
- `fix/<short-description>` — bug fixes
- `chore/<short-description>` — tooling, config, dependency updates
- `docs/<short-description>` — documentation only

---

### Day-to-Day Development Flow

```
1. Create a branch from main
   git checkout -b feature/motion-detection-improvements

2. Write code locally
   docker-compose -f infrastructure/docker-compose/docker-compose.yml up

3. Run E2E tests locally before pushing
   scripts/run-e2e-local.sh

4. Push the branch — pipeline runs lint + build + test env + e2e + destroy
   git push origin feature/motion-detection-improvements

5. Watch the pipeline in GitLab → CI/CD → Pipelines

6. Open a Merge Request into main
   - Assign reviewers
   - Attach the pipeline link showing tests passed
   - Describe what changed and why

7. After approval, merge into main
   - Pipeline runs again on main
   - After tests pass, pipeline auto-deploys to dev
   - Someone verifies dev, then clicks manual_approval to deploy to prod
```

---

### Merge Requests

Every change to `main` goes through a Merge Request. MRs should:

- Reference the work being done in the title (e.g. `Add sports motion detection tool`)
- Have a passing pipeline before merge — the MR pipeline runs the full ephemeral test cycle
- Be reviewed by at least one other person before merge
- Be squash-merged or merge-committed depending on the size of the change

**Main is always deployable.** If a pipeline on `main` fails after merge, the on-call person rolls back by re-running the deploy job from the last passing pipeline on `main`.

---

### Environments in GitLab

GitLab tracks deployments under **Deployments → Environments**:

| Environment | Updated by | What to use it for |
|---|---|---|
| `dev` | auto on every merge to `main` | Smoke testing new features, QA validation before prod |
| `prod` | manual approval only | Live system |

The Environments page shows the current deployed image tag for each environment and gives a one-click rollback to any previous deployment.

---

### Rolling Back a Production Deployment

If a production deployment introduces a regression:

1. Go to GitLab → **Deployments** → **Environments** → `prod`
2. Find the last known-good deployment in the history
3. Click **Re-deploy** on that deployment
4. This re-runs `deploy_prod` with the old image tag — ACA rolls back to the previous revision

Alternatively, via the Azure CLI:
```bash
# List revisions
az containerapp revision list \
  --name api-gateway \
  --resource-group video-extract-prod \
  --output table

# Activate the previous revision
az containerapp revision activate \
  --revision <previous-revision-name> \
  --resource-group video-extract-prod
```

---

### Infrastructure Changes (Terraform)

Terraform changes to `envs/dev/` or `envs/prod/` are **not applied automatically by the pipeline**. The pipeline only applies `envs/test/`. Infrastructure changes to dev and prod must be applied manually:

```bash
# Local machine or CI manual job
cd infrastructure/terraform/envs/dev
terraform plan
terraform apply
```

This is intentional — automated `terraform apply` on persistent environments carries risk of unintended resource destruction. Infrastructure PRs should be reviewed and applied manually.

When a Terraform change also involves application code changes, apply Terraform first, then let the CI pipeline deploy the application.

---

### Adding a New Service

When adding a new microservice to the platform:

1. Create the service directory under `backend/` or `mcp-servers/`
2. Add a `Dockerfile`
3. Add a `build:<service>` job to the `build_images` stage in `.gitlab-ci.yml`
4. Add the service to `SERVICES` arrays in `deploy_test_services`, `push_to_acr`, `deploy_dev`, and `deploy_prod`
5. Add an `azurerm_container_app` resource to `modules/aca/main.tf`
6. Apply Terraform to dev/prod manually
7. The new service will be included in all future pipeline runs

---

### Handling Secrets Rotation

When an API key or connection string needs to be rotated:

1. Generate the new credential in the relevant service (Azure portal / Anthropic console / etc.)
2. Update the GitLab CI variable — GitLab → Settings → CI/CD → Variables → edit the variable
3. For Azure resources used in dev/prod, also update the Key Vault secret:
   ```bash
   az keyvault secret set \
     --vault-name ve-dev-kv \
     --name anthropic-api-key \
     --value "sk-ant-new-key..."
   ```
4. Trigger a new pipeline or re-deploy from the Environments page to pick up the new value in ACA

---

### Pipeline Failure Triage

| Stage | Common failure | Fix |
|---|---|---|
| `lint` | Code style violation | Fix the lint error locally, push again |
| `build_images` | Docker build error | Check the Dockerfile and service dependencies |
| `aca_test_env_create` | Terraform error (quota, name collision) | Check Azure subscription quota; pipeline_id collision is very rare |
| `deploy_test_services` | ACA update timeout | Check ACA logs in Azure portal for the test resource group |
| `e2e_tests` | Test failure (non-blocking) | Check pytest output in the job log; download the `e2e-logs-{pipeline_id}` artifact from `collect_e2e_logs` for container-level diagnosis (see "How to download the logs" section above) |
| `collect_e2e_logs` | `az containerapp logs` returns nothing | Service may have crashed before logging; check `aca-status.log` in the artifact for replica state |
| `aca_test_env_destroy` | Terraform destroy fails | Manually destroy the resource group: `az group delete --name video-extract-test-<id>` |
| `deploy_dev` / `deploy_prod` | ACA image pull error | Verify the image exists in ACR with the right tag |

If `aca_test_env_destroy` fails, clean up manually to avoid orphaned Azure resources:
```bash
az group delete \
  --name "video-extract-test-${PIPELINE_ID}" \
  --yes --no-wait
```

---

## Completing a Release — Step-by-Step

This section is the human-side runbook for everything from `push_to_acr` onward. Stages 1–7 are fully automated; stages 8–10 require monitoring and smoke testing. Stage 11 (`deploy_prod`) is a placeholder — production deployment is not yet configured.

---

### Prerequisites (one-time setup)

Before any of these stages can succeed, the following must be in place:

1. **Azure service principal** — created with `Contributor` role on the subscription:
   ```bash
   az ad sp create-for-rbac \
     --name ve-gitlab-ci \
     --role Contributor \
     --scopes /subscriptions/<subscription_id> \
     --sdk-auth
   ```
   Save the output — it gives `clientId`, `clientSecret`, `tenantId`, `subscriptionId`.

2. **ACR admin credentials** — enable admin on the registry:
   ```bash
   az acr update --name videoextractdevacr --admin-enabled true
   az acr credential show --name videoextractdevacr
   ```

3. **GitLab CI variables set** (GitLab → Settings → CI/CD → Variables, all masked):
   - `ACR_REGISTRY`, `ACR_USERNAME`, `ACR_PASSWORD`
   - `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
   - `DB_ADMIN_PASSWORD`, `ANTHROPIC_API_KEY`, `TF_STATE_ACCESS_KEY`

4. **Persistent dev and prod environments exist** — created once via Terraform:
   ```bash
   cd infrastructure/terraform/envs/dev && terraform init && terraform apply
   cd infrastructure/terraform/envs/prod && terraform init && terraform apply
   ```
   These resource groups (`video-extract-dev`, `video-extract-prod`) are never destroyed by the pipeline.

5. **Terraform remote state storage** — storage account named `tfstatevideoextract` with a container named `tfstate`:
   ```bash
   az storage account create --name tfstatevideoextract --resource-group video-extract-shared --sku Standard_LRS
   az storage container create --name tfstate --account-name tfstatevideoextract
   ```

---

### Step 1 — Merge to `main`

The pipeline stages 1–7 run automatically on any branch push. Stages 8–11 are **gated to `main` only**.

> If your pipeline already ran **on `main`** (not a feature branch), skip directly to Step 2 — stages 8–11 are already running or queued.

To trigger stages 8–11 from a feature branch:

1. Open a Merge Request into `main` in GitLab
2. Ensure the MR pipeline (on your feature branch) has passed all stages including `aca_test_env_destroy`
3. Have a reviewer approve the MR
4. Merge — GitLab starts a new pipeline on `main`

The `main` pipeline runs stages 1–7 again (fresh build, fresh test env, fresh destroy), then continues to stages 8–11.

---

### Step 2 — Verify `push_to_acr`

This stage runs automatically after `aca_test_env_destroy` completes on `main`. It re-tags all 8 images as `:latest` in ACR.

**Check it worked:**

In the GitLab pipeline view, click the `push_to_acr` job and confirm all 8 `docker push` commands completed with `latest: digest: sha256:...`.

Or via the Azure CLI — check all 7 services at once:
```bash
ACR=videoextractdevacr   # replace with your ACR name
for svc in api-gateway agent-orchestrator preprocessing-worker \
           mcp-server-analysis mcp-server-processing angular-shell librechat; do
  echo "=== $svc ==="
  az acr repository show-tags \
    --name $ACR --repository $svc \
    --orderby time_desc --top 3 --output tsv
done
```

Each service should show both `<commit-sha>` and `latest` at the top of the list with the same digest.

**If this stage fails:**

The most common cause is ACR credential expiry. Re-generate the ACR password in the Azure portal (ACR → Access keys → Regenerate) and update `ACR_PASSWORD` in GitLab CI variables, then re-run the job from the GitLab pipeline UI.

---

### Step 3 — Monitor `deploy_dev`

This stage also runs automatically. It updates all 8 container apps in `video-extract-dev` to the new image.

**Watch the rollout:**

```bash
# Live tail — single service (Ctrl-C to exit)
watch -n 5 'az containerapp revision list \
  --name api-gateway \
  --resource-group video-extract-dev \
  --output table'
```

ACA performs a rolling update — the old revision stays running until the new one passes its health check. A successful rollout looks like:

```
Name                         Active    TrafficWeight    ProvisioningState    RunningState
---------------------------  --------  ---------------  -------------------  ------------
api-gateway--<old-sha>       False     0                Succeeded            Stopped
api-gateway--<new-sha>       True      100              Succeeded            Running
```

To check all 7 services at once (one-shot snapshot):

```bash
for svc in api-gateway agent-orchestrator preprocessing-worker \
           mcp-server-analysis mcp-server-processing angular-shell librechat; do
  echo "=== $svc ==="
  az containerapp revision list --name $svc --resource-group video-extract-dev --output table
done
```

**Check logs if a container fails to start:**

```bash
az containerapp logs show \
  --name api-gateway \
  --resource-group video-extract-dev \
  --tail 100 \
  --output table
```

---

### Step 4 — Smoke test dev

Before approving production deployment, verify the key surfaces on dev.

**Automated checks** (requires `az` CLI authenticated and `jq`):

```bash
scripts/smoke-test.sh dev
```

Checks: API Gateway `/health` → `{"status":"ok","db":"ok"}`, MCP tool catalogue (≥8 analysis tools, ≥4 processing tools), all 3 Service Bus queues have 0 dead-letter messages, all 7 container apps have ≥1 running replica. Exit code 0 = healthy.

**Manual checks** (not covered by the script):

- **Upload a test video** via the Angular shell (`https://<angular-shell-fqdn>`) and confirm:
  - SAS token is returned, upload completes, preprocessing worker indexes the video (`GET /v1/sessions/{id}/assets`)
- **Submit a job** via the LibreChat chat interface and confirm:
  - Job progresses `queued` → `running` → `completed`, output URL is reachable

If any check fails, fix forward by pushing another commit to `main` or roll back dev from the Environments page (see "Rolling Back" section).

After verifying dev, you may trigger `manual_approval` in the GitLab pipeline UI to allow the placeholder `deploy_prod` job to complete. Production deployment is not yet active.

---

### Quick-reference: full release command sequence

For a complete, healthy release from merge to dev verification:

```bash
# After merge to main — wait for pipeline stages 1–7 to complete, then:

# 1. Verify push_to_acr (images tagged :latest in ACR)
az acr repository show-tags --name videoextractdevacr --repository api-gateway --orderby time_desc --output table

# 2. Wait for dev rollout
az containerapp revision list --name api-gateway --resource-group video-extract-dev --output table

# 3. Smoke-test dev (health, tool catalogue, service bus dead-letters, replica state)
scripts/smoke-test.sh dev
```
