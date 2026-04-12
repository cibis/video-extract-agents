# Terraform Infrastructure — Detailed Reference

This document explains the Terraform layout, every module, every environment, and how the pieces connect.

---

## Directory Structure

```
infrastructure/terraform/
├── modules/                 Reusable building blocks (called from envs/)
│   ├── storage/             Azure Blob Storage account + videos container + Azure Files for PostgreSQL
│   ├── acr/                 Azure Container Registry
│   ├── aca/                 Azure Container Apps environment + all 9 container apps (incl. PostgreSQL)
│   ├── appinsights/         Application Insights workspace
│   ├── frontdoor/           Azure Front Door CDN profile + route
│   ├── appcommunication/    Azure Communication Services (email)
│   └── keyvault/            Azure Key Vault + all secrets
│
└── envs/                    Environment roots — each is a standalone Terraform workspace
    ├── dev/                 Persistent dev environment
    ├── prod/                Persistent prod environment
    └── test/                Ephemeral per-CI-pipeline environment
```

Each environment directory (`envs/dev/`, etc.) contains:
- `main.tf` — calls modules and defines top-level resources
- `variables.tf` — declares input variables
- `backend.tf` — remote state configuration (Azure Blob)

---

## Provider & State

**Provider:** `hashicorp/azurerm ~> 4.0`

Authentication uses environment variables (service principal in CI, `az login` locally):
```
ARM_CLIENT_ID
ARM_CLIENT_SECRET
ARM_SUBSCRIPTION_ID
ARM_TENANT_ID
```

**Remote state** is stored in a shared Azure Blob Storage account that must be bootstrapped once before any environment is applied (see [azure-credentials.md](azure-credentials.md)):

| Environment | State key |
|---|---|
| dev | `video-extract/dev/terraform.tfstate` |
| prod | `video-extract/prod/terraform.tfstate` |
| test | `video-extract/test/terraform.tfstate` |

All three backends point to the same storage account (`tfstatevideoextract`) and container (`tfstate`) in resource group `terraform-state-rg`.

---

## Environments

### `envs/dev/`

Persistent development environment. Destroyed and recreated manually only.

| Resource | Value |
|---|---|
| Resource group | `video-extract-dev` |
| Service Bus SKU | Standard |
| ACR SKU | Basic |
| PostgreSQL | Container (`postgres:15-alpine`), 32 GB Azure Files volume |
| Storage replication | LRS (default) |
| ACA min replicas | 0 (scale to zero; PostgreSQL fixed at 1) |
| ACA max replicas | 5 |
| Key Vault purge protection | false |

All secrets are stored in Key Vault. Application Insights and Front Door are provisioned.

---

### `envs/prod/`

Persistent production environment with higher-tier resources and redundancy.

| Resource | Value |
|---|---|
| Resource group | `video-extract-prod` |
| Service Bus SKU | Premium (capacity 1) |
| ACR SKU | Standard |
| PostgreSQL | Container (`postgres:15-alpine`), 128 GB Azure Files volume |
| Storage replication | ZRS (zone-redundant) |
| ACA min replicas | 1 (always warm) |
| ACA max replicas | 20 |
| Key Vault purge protection | true |

Prod differs from dev in: higher SKUs, zone-redundant storage, larger PostgreSQL volume (128 GB vs 32 GB), no scale-to-zero (`min_replicas = 1`), purge protection on Key Vault.

---

### `envs/test/`

**Ephemeral per-CI-pipeline environment.** Created at the start of each pipeline run, destroyed at the end regardless of test outcome.

Resource names include `${var.pipeline_id}` to avoid collisions between concurrent pipeline runs.

| Resource | Value |
|---|---|
| Resource group | `video-extract-test-<pipeline_id>` |
| Service Bus SKU | Standard |
| ACR SKU | Basic |
| PostgreSQL | Container (`postgres:15-alpine`), 32 GB Azure Files volume |
| Storage replication | LRS |
| ACA min replicas | 0 (PostgreSQL fixed at 1) |
| ACA max replicas | 3 |
| App Insights | **not provisioned** (`appinsights_connection_string = ""`) |
| Front Door | **not provisioned** (`front_door_url = ""`) |
| Key Vault | **not provisioned** (secrets injected directly as env vars via `TF_VAR_*`) |

Tags include `ttl = "2h"` for cost safety. The test env has no `entra_tenant_id` / `entra_client_id` in its variables — tests run without auth or with a test identity injected via `LOCAL_DEV_SKIP_AUTH`.

**Extra variable:** `pipeline_id` (string) — passed as `TF_VAR_pipeline_id` by GitLab CI. Ensures every resource in a test run has a unique name.

---

## Modules

### `modules/storage/`

Creates one Azure Blob Storage account, one blob container, and exposes storage credentials used by the ACA module to back the PostgreSQL Azure Files volume.

**Resources:**
- `azurerm_storage_account.main` — account name: `videoextract<env><6-char-random-suffix>`
  - TLS 1.2 minimum, HTTPS only
  - Blob versioning enabled, 7-day soft delete
  - Replication: LRS by default, overridden to ZRS in prod
- `azurerm_storage_container.videos` — private container named `videos`
- `random_string.suffix` — 6-char lowercase suffix to ensure globally unique account name

**Key outputs:** `primary_connection_string`, `storage_account_name`, `storage_account_id`, `primary_access_key` — all wired into the `aca` module; connection string also goes to Key Vault

---

### `modules/acr/`

Creates an Azure Container Registry.

**Resources:**
- `azurerm_container_registry.main` — name: `videoextract<env>acr`
  - Admin access enabled (credentials used by ACA to pull images)
  - SKU: Basic (dev/test), Standard (prod)

**Key outputs:** `login_server`, `admin_username`, `admin_password` — all wired into the `aca` module for image pull credentials

---

### `modules/aca/`

The largest module. Creates the Container Apps environment, all container app services, and the PostgreSQL database container with Azure Files persistence.

#### Environment-level resources

- `azurerm_log_analytics_workspace.main` — `videoextract-<env>-law`
  - PerGB2018 SKU, 30-day retention
  - Required by the Container Apps environment for log ingestion
- `azurerm_container_app_environment.main` — `videoextract-<env>-cae`
  - Linked to the Log Analytics workspace
  - All container apps in this module are deployed into this environment
- `azurerm_storage_share.postgres_data` — Azure Files share (`postgres-data`) in the storage account; quota: `db_storage_gb` (default 32 GB, 128 GB in prod)
- `azurerm_container_app_environment_storage.postgres_data` — registers the Azure Files share with the ACA environment so it can be volume-mounted
- `DATABASE_URL` is constructed internally as `postgresql+asyncpg://<user>:<pass>@postgresql:5432/videoextract`; Node.js api-gateway uses the plain `postgresql://` variant. No `database_url` variable is accepted from outside.

#### Container Apps

Each container app follows the same pattern:
- **Registry block** — pull credentials from ACR (via `acr-password` secret)
- **Template block** — container spec (image, CPU, memory, env vars) + scaling rules
- **Ingress block** — HTTP ingress config (internal or external)

##### `postgresql`
- Image: `postgres:15-alpine` (public Docker Hub)
- CPU: 0.5 vCPU, 1 GiB memory
- **No external ingress** — internal TCP on port 5432 only
- **Fixed at `min_replicas = 1`, `max_replicas = 1`** — stateful service; must not scale to zero or run multiple replicas
- Azure Files volume mounted at `/var/lib/postgresql/data`; `PGDATA` set to `/var/lib/postgresql/data/pgdata` (subdirectory avoids Azure Files `lost+found` issue)
- `db_admin_password` injected as a container secret
- All DB-consuming services (`api-gateway`, `agent-orchestrator`, `preprocessing-worker`, `notification-worker`) have `depends_on = [azurerm_container_app.postgresql]`
- **Note:** no managed backups. Azure Files point-in-time restore or snapshots can be used if backup is needed.

##### `api-gateway`
- Image: `api-gateway:<image_tag>`
- CPU: 0.5 vCPU, 1 GiB memory
- **External ingress** on port 8000 — the only publicly reachable service
- Scale rule: HTTP concurrency (50 concurrent requests triggers scale-out)
- Key env vars: `DATABASE_URL`, `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_SERVICE_BUS_CONNECTION_STRING`, `OUTPUT_URL_MODE=frontdoor`, `FRONT_DOOR_URL`, `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `AGENT_ORCHESTRATOR_URL=http://agent-orchestrator`
- Note: `DATABASE_URL` strips `+asyncpg` (Node.js uses the plain `pg` driver)

##### `agent-orchestrator`
- Image: `agent-orchestrator:<image_tag>`
- CPU: 1.0 vCPU, 2 GiB memory
- **Internal ingress** on port 8001 — only reachable by api-gateway within the ACA environment
- Scale rule: **KEDA Service Bus** — scales on `job-queued` queue depth (1 replica per 5 messages)
- Key env vars: `AGENT_MODEL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AWS_*`, `MCP_ANALYSIS_URL=http://mcp-server-analysis`, `MCP_PROCESSING_URL=http://mcp-server-processing`

##### `mcp-server-analysis`
- Image: `mcp-server-analysis:<image_tag>`
- CPU: 0.5 vCPU, 1 GiB memory
- **Internal ingress** on port 8100
- Scale rule: HTTP concurrency (20 concurrent requests)
- Key env vars: `TOOL_FRONTIER_MODEL`, `MODEL_ALIASES_OVERRIDE`, `ANTHROPIC_API_KEY`, `AWS_*`

##### `mcp-server-processing`
- Image: `mcp-server-processing:<image_tag>`
- CPU: 1.0 vCPU, 2 GiB memory (video processing is memory-intensive)
- **Internal ingress** on port 8200
- Scale rule: **KEDA Service Bus** — scales on `job-queued` queue depth (1 replica per 5 messages)
- Key env vars: `AZURE_STORAGE_CONNECTION_STRING` only

##### `preprocessing-worker`
- Image: `preprocessing-worker:<image_tag>`
- CPU: 1.0 vCPU, 2 GiB memory
- **No ingress** — purely event-driven
- Scale rule: **KEDA Service Bus** — scales on `video-uploaded` queue depth
- Key env vars: `DATABASE_URL`, `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_SERVICE_BUS_CONNECTION_STRING`

##### `notification-worker`
- Image: `notification-worker:<image_tag>`
- CPU: 0.25 vCPU, 0.5 GiB memory (lightweight email dispatch)
- **No ingress** — purely event-driven
- Scale rule: **KEDA Service Bus** — scales on `job-completed` queue depth
- Key env vars: `NOTIFICATION_MODE=acs`, `AZURE_COMMUNICATION_SERVICES_CONNECTION_STRING`, `FRONT_DOOR_HOSTNAME`

##### `angular-shell`
- Image: `angular-shell:<image_tag>`
- CPU: 0.25 vCPU, 0.5 GiB memory
- **External ingress** on port 80
- Scale rule: HTTP concurrency (50)
- Key env vars: `API_GATEWAY_URL=http://api-gateway`, `LIBRECHAT_URL=http://librechat`

##### `librechat`
- Image: `librechat:<image_tag>`
- CPU: 0.5 vCPU, 1 GiB memory
- **External ingress** on port 3080 — must be browser-accessible because it is iframe-embedded
- Scale rule: HTTP concurrency (50)
- Key env vars: `API_GATEWAY_URL=http://api-gateway`

**Key output:** `api_gateway_fqdn` — used by the `frontdoor` module as origin; `log_analytics_workspace_id` — used by the `appinsights` module

---

### `modules/appinsights/`

Creates an Application Insights workspace attached to the ACA Log Analytics workspace.

**Resources:**
- `azurerm_application_insights.main` — `videoextract-<env>-ai`
  - Type: `web`
  - Linked to the Log Analytics workspace created by the `aca` module
  - One resource per environment (dev and prod each get their own; test skips this module)

**Key output:** `connection_string` — injected as `APPLICATIONINSIGHTS_CONNECTION_STRING` into all container apps

---

### `modules/frontdoor/`

Creates an Azure Front Door CDN profile that sits in front of the API gateway.

**Resources:**
- `azurerm_cdn_frontdoor_profile.main` — `videoextract-<env>-fd`, Standard SKU
- `azurerm_cdn_frontdoor_endpoint.main` — `videoextract-<env>`
- `azurerm_cdn_frontdoor_origin_group.main` — `api-gateway-og`
- `azurerm_cdn_frontdoor_origin.api_gateway` — points to the ACA api-gateway FQDN
  - HTTPS port 443, priority 1, weight 1000
- `azurerm_cdn_frontdoor_route.main` — matches `/*`, HTTP→HTTPS redirect, forwards to api-gateway

**Key output:** `endpoint_hostname` — the Front Door public hostname, injected as `FRONT_DOOR_URL` / `FRONT_DOOR_HOSTNAME` in container apps

> **Not provisioned for test environments** — too slow to create per pipeline run

---

### `modules/appcommunication/`

Creates an Azure Communication Services resource for transactional email.

**Resources:**
- `azurerm_communication_service.main` — `videoextract-<env>-acs`
  - Data location: United States

**Key output:** `primary_connection_string` — injected into `notification-worker` as `AZURE_COMMUNICATION_SERVICES_CONNECTION_STRING`

> After Terraform creates the resource, you must manually verify a sender email domain in the ACS portal before emails can be sent.

---

### `modules/keyvault/`

Creates an Azure Key Vault and stores all sensitive values as secrets.

**Resources:**
- `azurerm_key_vault.main` — `ve-<env>-kv`
  - Standard SKU
  - 7-day soft delete retention
  - Purge protection: false (dev), true (prod)
  - Access policy: grants the Terraform service principal full secret management rights
- Secrets stored:
  - `anthropic-api-key`
  - `db-password`
  - `storage-connection-string`
  - `servicebus-connection-string`
  - `acs-connection-string`
  - `appinsights-connection-string` (only when connection string is non-empty)

> **Phase B note:** The Key Vault exists and all secrets are populated. ACA containers currently receive secret values as plain env vars injected by Terraform (fast path). The next phase will wire ACA managed identities with Key Vault Secrets User role + `key_vault_secret_id` secret references, so containers fetch secrets at runtime rather than having them baked in at deploy time.

> **Not provisioned for test environments** — test containers receive secrets directly via `TF_VAR_*` env vars in CI.

---

## Data Flow Between Modules

```
envs/dev/main.tf
│
├── module.storage         → primary_connection_string, storage_account_name,
│                             storage_account_id, primary_access_key
│                               ↓ (to module.aca + module.keyvault)
├── azurerm_servicebus_namespace
│                          → default_primary_connection_string
│                               ↓ (to module.aca + module.keyvault)
├── module.acr             → login_server, admin_username, admin_password
│                               ↓ (to module.aca)
├── module.aca             → log_analytics_workspace_id
│   (also creates              ↓ (to module.appinsights)
│    PostgreSQL container)
│                          → api_gateway_fqdn
│                               ↓ (to module.frontdoor)
├── module.appinsights     → connection_string
│                               ↓ (to module.aca + module.keyvault)
├── module.frontdoor       → endpoint_hostname
│                               ↓ (to module.aca)
├── module.appcommunication → primary_connection_string
│                               ↓ (to module.aca + module.keyvault)
└── module.keyvault        ← all secrets from above (stored for future managed identity wiring)
```

---

## KEDA Autoscaling

Four services use KEDA Service Bus queue-depth scaling via `custom_scale_rule` blocks:

| Service | Queue monitored | Threshold |
|---|---|---|
| `agent-orchestrator` | `job-queued` | 1 replica per 5 messages |
| `mcp-server-processing` | `job-queued` | 1 replica per 5 messages |
| `preprocessing-worker` | `video-uploaded` | 1 replica per 5 messages |
| `notification-worker` | `job-completed` | 1 replica per 5 messages |

Four services use HTTP concurrency scaling via `http_scale_rule` blocks:

| Service | Concurrent requests threshold |
|---|---|
| `api-gateway` | 50 |
| `mcp-server-analysis` | 20 |
| `angular-shell` | 50 |
| `librechat` | 50 |

All services scale to zero when idle (`min_replicas = 0`) in dev and test, except `postgresql` which is hardcoded to `min_replicas = 1` in all environments. In prod, `min_replicas = 1` keeps all services warm.

---

## Running Terraform

### First time (bootstrap state storage first)

```bash
# See azure-credentials.md for bootstrap commands

# Authenticate
az login
export ARM_SUBSCRIPTION_ID=<sub-id>
export ARM_TENANT_ID=<tenant-id>
```

### Dev environment

```bash
cd infrastructure/terraform/envs/dev
terraform init
terraform plan -var="db_admin_password=..." -var="anthropic_api_key=sk-ant-..."
terraform apply
```

Or using a `terraform.tfvars` file (never commit this):
```hcl
db_admin_password   = "your-secure-password"
anthropic_api_key   = "sk-ant-..."
entra_tenant_id     = "your-tenant-id"
entra_client_id     = "your-client-id"
```

### Prod environment

```bash
cd infrastructure/terraform/envs/prod
terraform init
terraform plan -var="image_tag=v1.2.3" ...
terraform apply
```

### Test environment (CI only)

```bash
cd infrastructure/terraform/envs/test
terraform init
terraform apply -var="pipeline_id=${CI_PIPELINE_ID}" -var="image_tag=${CI_COMMIT_SHA}"
# ... run tests ...
terraform destroy -var="pipeline_id=${CI_PIPELINE_ID}" -auto-approve
```

### Useful commands

```bash
# See all outputs (connection strings, hostnames)
terraform output

# Target a single resource (e.g. re-deploy just the PostgreSQL container app)
terraform apply -target=azurerm_container_app.postgresql

# Destroy just the test environment
terraform destroy -auto-approve
```

---

## Accessing the UI Per Environment

### Local (docker-compose)

All services are reachable on localhost directly:

| Surface | URL | Notes |
|---|---|---|
| Angular shell | `http://localhost:4200` | Primary entry point; includes upload + job dashboard |
| LibreChat (chat iframe) | `http://localhost:3080` | Also accessible standalone |
| API Gateway | `http://localhost:8000` | REST API; auth bypassed via `LOCAL_DEV_SKIP_AUTH=true` |

No login required locally — auth middleware injects a static dev identity automatically.

---

### Dev environment

Two ACA container apps have external ingress. Retrieve their hostnames after `terraform apply`:

```bash
cd infrastructure/terraform/envs/dev

# Show all outputs including FQDNs and the Front Door hostname
terraform output
```

| Surface | How to reach it | Notes |
|---|---|---|
| Angular shell | ACA FQDN for `angular-shell` — shown in `terraform output` | External ingress, port 80, HTTPS |
| LibreChat (standalone) | ACA FQDN for `librechat` — shown in `terraform output` | External ingress, port 3080 |
| API Gateway (direct) | ACA FQDN for `api-gateway` — shown in `terraform output` | External ingress, port 8000 |
| API Gateway (via CDN) | `https://<terraform output front_door_hostname>` | Front Door sits in front of api-gateway; use this URL for production-like testing |

You can also look up FQDNs in the Azure Portal: **Container Apps** → select the app → **Overview** → **Application URL**.

**Auth:** Azure Entra External ID magic link login. Use an account in the dev Entra tenant. The tenant and client IDs come from the `entra_tenant_id` / `entra_client_id` variables applied at deploy time.

---

### Prod environment

Same approach as dev — retrieve FQDNs from `terraform output` in `envs/prod/`.

```bash
cd infrastructure/terraform/envs/prod
terraform output
```

| Surface | How to reach it |
|---|---|
| Angular shell | `angular-shell` ACA FQDN (external ingress, port 80) |
| LibreChat (standalone) | `librechat` ACA FQDN (external ingress, port 3080) |
| API Gateway (via CDN) | `https://<terraform output front_door_hostname>` |

**Auth:** Same Entra External ID magic link flow — use a prod-tenant account.

> Front Door is the correct public entry for API traffic in both dev and prod. Direct ACA FQDNs bypass CDN caching and WAF; prefer Front Door for realistic testing.

---

### Test environment (ephemeral / CI)

The test environment is created and destroyed automatically by the CI pipeline. It is designed for automated E2E tests, not interactive browser use. However, if you need to access it manually during a pipeline run:

1. Find the pipeline's ACA resource group: `video-extract-test-<pipeline_id>`
2. In the Azure Portal, navigate to **Container Apps** within that resource group
3. Open `api-gateway` → **Application URL** for the API endpoint
4. Open `angular-shell` → **Application URL** for the shell (external ingress, port 80)

> Front Door is **not** provisioned for test environments. The `api-gateway` ACA FQDN is the API entry point directly.

**Auth:** `LOCAL_DEV_SKIP_AUTH` is unset in CI — test environments use real Entra auth or a test identity injected via the CI service principal. Interactive browser login is not available; test flows use service credentials.

---

## Tagging Strategy

All resources are tagged consistently for cost allocation and automation:

| Tag | Dev | Prod | Test |
|---|---|---|---|
| `environment` | `dev` | `prod` | `test` |
| `project` | `video-extract` | `video-extract` | `video-extract` |
| `managed-by` | `terraform` | `terraform` | `ci` |
| `pipeline-id` | — | — | `<pipeline_id>` |
| `ttl` | — | — | `2h` |

The `ttl` tag on test resources signals to any automated cleanup job that these resources can be force-deleted if the CI destroy step fails.
