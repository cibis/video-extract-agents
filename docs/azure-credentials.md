# Azure Credentials & Keys — Setup Guide

This document covers every credential the platform needs, with step-by-step instructions for creating each Azure resource from scratch, retrieving the connection string or key, and setting it in the right place.

> **Terraform note:** For dev and prod environments, Terraform creates most of these resources automatically. Follow the manual steps in this document if you need to create resources manually, understand what Terraform is doing, or set up credentials for CI/CD.

---

## Prerequisites

### Install the Azure CLI

**Windows:**
```powershell
winget install Microsoft.AzureCLI
```

**macOS:**
```bash
brew install azure-cli
```

**Linux:**
```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

Verify:
```bash
az --version
```

### Sign in

```bash
az login
```

This opens a browser for interactive login. After signing in:

```bash
# List subscriptions
az account list --output table

# Set the subscription you want to use
az account set --subscription "<subscription-name-or-id>"

# Verify
az account show
```

### Get your subscription and tenant IDs (you'll need these throughout)

```bash
az account show --query "{subscriptionId:id, tenantId:tenantId}" -o table
```

---

## Overview of what needs to be created

| # | Resource | Terraform? | Manual required | Role |
|---|---|---|---|---|
| 1 | Resource Group | yes | once (or use Terraform) | Logical container that groups all Azure resources for a given environment (dev, prod, test). All billing, access control, and lifecycle management is applied at this level. Deleting the resource group deletes everything in it. |
| 2 | Azure Blob Storage Account | yes | no — Terraform handles it | Stores all binary data for the platform: original uploaded videos, FFmpeg-extracted keyframes, intermediate segments, and final processed output videos. Also hosts the keyframe index JSON files that agents read instead of raw video. |
| 3 | Azure Service Bus Namespace | yes | no — Terraform handles it | Message broker that decouples all backend services. Carries five queues: `video-uploaded` → `video-indexed` → `job-queued` → `job-completed` / `job-failed`. Services publish events and consume them independently — no direct service-to-service calls for async flows. |
| 4 | PostgreSQL 15 (ACA container) | yes | no — Terraform handles it | Relational database storing all structured metadata: users, videos, sessions, jobs, job steps, keyframe index rows, assets, and output records. The source of truth for job status and the keyframe index that agents query at crew startup. Runs as a `postgres:15-alpine` container inside Azure Container Apps, persisted via an Azure Files share on the storage account. |
| 5 | Azure Container Registry | yes | no — Terraform handles it | Private Docker image registry. GitLab CI builds and pushes all 8 service images here, tagged with the commit SHA. Azure Container Apps pulls images from here on deploy. No images are ever pulled from Docker Hub in CI/prod. |
| 6 | Azure Entra External ID App Registration | **NO** | **yes — always manual** | Identity provider for end users. Issues signed JWTs via magic-link email (passwordless). The api-gateway validates every inbound JWT against Entra's JWKS endpoint. This is a tenant-level resource that cannot be managed by a subscription-scoped Terraform service principal. |
| 7 | Azure Front Door | yes | no — Terraform handles it | CDN and global entry point that sits in front of the api-gateway. Handles HTTPS termination, HTTP→HTTPS redirect, and WAF rules. Also generates HMAC-signed time-limited download URLs for output videos — the api-gateway and notification-worker use these signed URLs so videos are never served without expiry. |
| 8 | Azure Communication Services | yes | email domain verification manual | Transactional email service. The notification-worker calls ACS to send job completion and job failure emails containing the signed output video download link. Terraform creates the resource; sender domain verification requires a manual DNS step. |
| 9 | Application Insights | yes | no — Terraform handles it | Telemetry and distributed tracing backend. All services emit traces, dependency calls, and request metrics via auto-instrumentation (no custom spans). Provides end-to-end request maps, failure rates, and latency dashboards in the Azure portal. One instance per environment (dev + prod); skipped for ephemeral test runs. |
| 10 | Azure Key Vault | yes | no — Terraform handles it | Centralised secret store. Terraform writes all generated connection strings and API keys here at apply time. In Phase B, ACA containers will fetch secrets directly from Key Vault via managed identity at runtime rather than having them baked in as env vars. |
| 11 | Terraform Service Principal | **NO** | **yes — must be created before Terraform runs** | Azure AD identity (service account) used by Terraform and GitLab CI to authenticate to Azure. Granted Contributor role on the subscription so it can create, update, and delete all platform resources. Its credentials (`ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`) are stored as masked GitLab CI variables. |
| 12 | Terraform State Storage | **NO** | **yes — must be created before Terraform runs** | Azure Blob Storage account (`tfstatevideoextract`) that holds Terraform's state files for all three environments. Must exist before `terraform init` can run. Kept in a separate resource group (`terraform-state-rg`) so it is never accidentally destroyed when environment resource groups are torn down. |

---

## 1. Terraform Service Principal

Terraform needs an identity with permission to create Azure resources. This must exist before you run `terraform apply` for the first time.

### Create via Azure CLI

```bash
# Replace <subscription-id> with your actual subscription ID
az ad sp create-for-rbac \
  --name "video-extract-terraform" \
  --role Contributor \
  --scopes /subscriptions/<subscription-id>
```

Output:
```json
{
  "appId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "displayName": "video-extract-terraform",
  "password": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "tenant": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

The four fields from this output map to four variables that must be set in two places — your local shell for manual Terraform runs, and GitLab CI for the automated pipeline.

### Local Terraform runs — shell environment

When running `terraform plan` or `terraform apply` locally from `infrastructure/terraform/envs/dev/` or `infrastructure/terraform/envs/prod/`, these must be exported in your shell session before running any Terraform command. Add them to your shell profile (`~/.bashrc`, `~/.zshrc`) or to a local secrets file you source manually — never commit them:

```bash
# Add to ~/.bashrc or ~/.zshrc, or source from a local secrets file
export ARM_CLIENT_ID="<appId>"
export ARM_CLIENT_SECRET="<password>"
export ARM_TENANT_ID="<tenant>"
export ARM_SUBSCRIPTION_ID="<subscription-id>"
```

These four variables are read directly by the `azurerm` Terraform provider — no project file references them by name, they are a convention of the provider itself.

### GitLab CI — project CI/CD variables

Navigate to your GitLab project → **Settings** → **CI/CD** → **Variables** → **Add variable**.

For each variable: set **Key** and **Value**, check **Masked** (hides value in job logs), check **Protected** if you want it restricted to protected branches only.

| Variable | Value from CLI output | Masked |
|---|---|---|
| `ARM_CLIENT_ID` | `appId` | yes |
| `ARM_CLIENT_SECRET` | `password` | yes |
| `ARM_TENANT_ID` | `tenant` | yes |
| `ARM_SUBSCRIPTION_ID` | your subscription ID (from `az account show`) | yes |

The pipeline reads these in `.gitlab-ci.yml` inside the `&terraform-setup` anchor:
```yaml
- export ARM_CLIENT_ID="$AZURE_CLIENT_ID"
- export ARM_CLIENT_SECRET="$AZURE_CLIENT_SECRET"
- export ARM_TENANT_ID="$AZURE_TENANT_ID"
- export ARM_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID"
```

---

## 2. Terraform State Storage

Terraform stores state in Azure Blob. This storage account must exist before `terraform init` can run.

### Create via Azure CLI

```bash
# Create a dedicated resource group for state (separate from app resources)
az group create \
  --name terraform-state-rg \
  --location eastus

# Create the storage account
# Name must be globally unique, 3-24 lowercase alphanumeric chars
az storage account create \
  --name tfstatevideoextract \
  --resource-group terraform-state-rg \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2

# Create the container for state files
az storage container create \
  --name tfstate \
  --account-name tfstatevideoextract
```

### Create via Azure portal

1. Search for **Storage accounts** → **Create**
2. **Basics:**
   - Resource group: `terraform-state-rg` (create new)
   - Storage account name: `tfstatevideoextract`
   - Region: East US
   - Performance: Standard
   - Redundancy: LRS
3. **Advanced:** Minimum TLS version → TLS 1.2
4. **Review + create** → **Create**
5. Once created: go to the storage account → **Containers** → **+ Container**
   - Name: `tfstate`
   - Access level: Private

This matches the `backend.tf` configuration in all three environment directories.

---

## 3. Azure Blob Storage Account

**Used for:** uploading videos, storing keyframes, segments, and processed outputs.

> Terraform creates this automatically via `modules/storage`. Follow these steps only if creating manually.

### Create via Azure CLI

```bash
# Create resource group (skip if already exists)
az group create --name video-extract-dev --location eastus

# Create storage account
az storage account create \
  --name videoextractdev<random6chars> \
  --resource-group video-extract-dev \
  --location eastus \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2 \
  --https-only true

# Create the videos container
az storage container create \
  --name videos \
  --account-name videoextractdev<random6chars> \
  --public-access off
```

### Create via Azure portal

1. Search **Storage accounts** → **Create**
2. **Basics:**
   - Resource group: `video-extract-dev`
   - Storage account name: `videoextractdev<6chars>` (must be globally unique)
   - Region: East US
   - Redundancy: LRS (dev) or ZRS (prod)
3. **Advanced:** Minimum TLS → 1.2, Secure transfer required → Enabled
4. **Review + create** → **Create**
5. After creation: go to **Containers** → **+ Container**
   - Name: `videos`
   - Access level: Private

### Get the connection string

**Portal:**
1. Storage account → **Security + networking** → **Access keys**
2. Click **Show** next to `key1`
3. Copy the full **Connection string**

**CLI:**
```bash
az storage account show-connection-string \
  --name <storage-account-name> \
  --resource-group video-extract-dev \
  --query connectionString \
  --output tsv
```

### Where to set it

| Context | File | Variable |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` | `AZURE_STORAGE_CONNECTION_STRING` |
| CI/GitLab var | GitLab → **Settings** → **CI/CD** → **Variables** (not needed — Terraform reads this directly from the resource it creates) | — |
| Azure/ACA | Terraform injects automatically — value comes from `modules/storage` output `primary_connection_string`, wired in `infrastructure/terraform/envs/dev/main.tf` → `module.aca.storage_connection_string` | `AZURE_STORAGE_CONNECTION_STRING` |

> Local dev uses Azurite — the connection string is already pre-set in `infrastructure/docker-compose/.env.example`, do not change it.

---

## 4. Azure Service Bus Namespace & Queues

**Used for:** async event passing between all backend services.

> Terraform creates this automatically. Follow these steps only if creating manually.

### Create via Azure CLI

```bash
# Create namespace
az servicebus namespace create \
  --name videoextract-dev-servicebus \
  --resource-group video-extract-dev \
  --location eastus \
  --sku Standard

# Create the five required queues
for queue in video-uploaded video-indexed job-queued job-completed job-failed; do
  az servicebus queue create \
    --name $queue \
    --namespace-name videoextract-dev-servicebus \
    --resource-group video-extract-dev \
    --max-delivery-count 10
done
```

### Create via Azure portal

1. Search **Service Bus** → **Create**
2. **Basics:**
   - Resource group: `video-extract-dev`
   - Namespace name: `videoextract-dev-servicebus`
   - Location: East US
   - Pricing tier: Standard (dev) / Premium (prod)
3. **Review + create** → **Create**
4. After creation: go to the namespace → **Queues** → **+ Queue**

   Create each of the following queues (Max delivery count: 10):
   - `video-uploaded`
   - `video-indexed`
   - `job-queued`
   - `job-completed`
   - `job-failed`

### Get the connection string

**Portal:**
1. Service Bus namespace → **Settings** → **Shared access policies**
2. Click **RootManageSharedAccessKey**
3. Copy **Primary Connection String**

**CLI:**
```bash
az servicebus namespace authorization-rule keys list \
  --name RootManageSharedAccessKey \
  --namespace-name videoextract-dev-servicebus \
  --resource-group video-extract-dev \
  --query primaryConnectionString \
  --output tsv
```

### Where to set it

| Context | File | Variable |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` | `AZURE_SERVICE_BUS_CONNECTION_STRING` |
| CI/GitLab var | GitLab → **Settings** → **CI/CD** → **Variables** (not needed — Terraform reads this directly from the namespace resource it creates) | — |
| Azure/ACA | Terraform injects automatically — value comes from `azurerm_servicebus_namespace.main.default_primary_connection_string` in `infrastructure/terraform/envs/dev/main.tf`, wired to all services via `module.aca.service_bus_connection_string` | `AZURE_SERVICE_BUS_CONNECTION_STRING` |

---

## 5. PostgreSQL (ACA container)

**Used for:** all metadata — users, videos, jobs, keyframe index.

PostgreSQL runs as a `postgres:15-alpine` container app inside the Azure Container Apps environment — the same image and configuration used in docker-compose local development. There is no separate Azure Database for PostgreSQL service.

**Terraform creates and manages everything automatically:**
- An Azure Files share (`postgres-data`) in the existing storage account (dev: 32 GB, prod: 128 GB)
- An ACA environment storage binding that mounts the share as a volume
- A `postgresql` container app with the volume mounted at `/var/lib/postgresql/data`

No manual creation steps are required. The container is reachable by other services inside the ACA environment at hostname `postgresql:5432`.

### DATABASE_URL

`DATABASE_URL` is constructed entirely inside Terraform (`modules/aca/main.tf`) and injected into every container as an environment variable. You never set it manually in CI or Azure.

| Context | File | Variable |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` | `DATABASE_URL` |
| CI/GitLab var | Not needed — Terraform builds it internally from `db_admin_password` | — |
| Azure/ACA | Terraform injects automatically — built as `postgresql+asyncpg://<user>:<pass>@postgresql:5432/videoextract`; Node.js api-gateway uses the `postgresql://` variant (no `+asyncpg`) | `DATABASE_URL` |

### DB admin password

The password is a sensitive Terraform input variable (`db_admin_password`) declared in each env's `variables.tf`. Set it in one of two ways:
- **Locally:** create `infrastructure/terraform/envs/dev/terraform.tfvars` (gitignored) and add `db_admin_password = "g7$N9#vA2xP8zLqV!wM1nB9y"`
- **CI:** add a masked GitLab variable `TF_VAR_db_admin_password` — the `TF_VAR_` prefix makes Terraform pick it up automatically

### Initialising the schema

After the first `terraform apply`, the PostgreSQL container starts empty. Run `init_db.py` once to create all tables:

```bash
# Exec into the postgresql container app (requires az CLI + containerapp extension)
az containerapp exec \
  --name postgresql \
  --resource-group video-extract-dev \
  --command "psql -U psqladmin -d videoextract"

# Or run init_db.py from any machine that can reach the ACA environment:
DB_PASSWORD=$(terraform -chdir=infrastructure/terraform/envs/dev output -raw db_admin_password)
DATABASE_URL="postgresql://psqladmin:${DB_PASSWORD}@<postgresql-internal-fqdn>:5432/videoextract" \
  python scripts/init_db.py
```

> **Note:** The internal FQDN of the `postgresql` container app within the ACA environment is `postgresql.<environment-default-domain>`. Other services inside the same ACA environment resolve it simply as `postgresql`. External access (e.g. from a dev machine) requires the full FQDN or an `az containerapp exec` tunnel.

---

## 6. Azure Entra External ID — App Registration

**Used for:** issuing magic-link JWT tokens; api-gateway validates every JWT against this.

This is the **only resource Terraform never provisions** — it is a tenant-level identity resource set up once manually.

### Step 1 — Enable Entra External ID on your tenant

1. In the Azure portal, search for **Microsoft Entra External ID**
2. If prompted to create an External ID tenant, choose **Create a new external tenant** or use an existing one
3. Note the **Tenant ID** from the Overview page

### Step 2 — Register the application

**Portal:**
1. Inside your Entra External ID tenant, go to **App registrations** → **New registration**
2. Fill in:
   - Name: `video-extract-api`
   - Supported account types: **Accounts in this organizational directory only**
   - Redirect URI: leave blank for now
3. Click **Register**
4. On the Overview page, copy:
   - **Application (client) ID** → this is your `AZURE_ENTRA_CLIENT_ID`
   - **Directory (tenant) ID** → this is your `AZURE_ENTRA_TENANT_ID`

**CLI:**
```bash
# Create the app registration
az ad app create --display-name "video-extract-api"

# Get the app ID
az ad app list \
  --display-name "video-extract-api" \
  --query "[0].appId" \
  --output tsv

# Get the tenant ID
az account show --query tenantId --output tsv
```

### Step 3 — Create a user flow (magic link / email OTP)

1. In your Entra External ID tenant, go to **User flows** → **New user flow**
2. Choose **Sign up and sign in**
3. Under **Identity providers**, select **Email one-time passcode**
4. Configure the user flow and click **Create**

### Step 4 — Expose an API scope (required for JWT audience validation)

1. App registration → **Expose an API**
2. Click **Set** next to Application ID URI → accept the default (`api://<client-id>`)
3. Click **Add a scope**:
   - Scope name: `access_as_user`
   - Who can consent: Admins and users
   - Click **Add scope**

### Construct the JWKS URI

The api-gateway uses this URI to fetch Entra's public signing keys:

```
https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
```

### Where to set it

| Variable | Local dev file | CI | Azure (Terraform) |
|---|---|---|---|
| `AZURE_ENTRA_TENANT_ID` | not needed — set `LOCAL_DEV_SKIP_AUTH=true` in `infrastructure/docker-compose/.env` | GitLab masked variable `TF_VAR_entra_tenant_id` — picked up by `infrastructure/terraform/envs/dev/variables.tf` → `entra_tenant_id` | Terraform passes it to `infrastructure/terraform/modules/aca/main.tf` → injected as `ENTRA_TENANT_ID` env var on the `api-gateway` container |
| `AZURE_ENTRA_CLIENT_ID` | not needed — see above | GitLab masked variable `TF_VAR_entra_client_id` — picked up by `infrastructure/terraform/envs/dev/variables.tf` → `entra_client_id` | Terraform passes it to `infrastructure/terraform/modules/aca/main.tf` → injected as `ENTRA_CLIENT_ID` env var on the `api-gateway` container |
| `AZURE_ENTRA_JWKS_URI` | not needed — see above | derived at runtime from tenant ID inside `backend/api-gateway/src/middleware/auth.ts` | derived at runtime — not a separate env var |

> **Local dev:** Set `LOCAL_DEV_SKIP_AUTH=true` in `infrastructure/docker-compose/.env` — the api-gateway (`backend/api-gateway/src/middleware/auth.ts`) skips all JWT validation and injects a static dev identity `{ id: "00000000-0000-0000-0000-000000000001", email: "dev@local" }`.

---

## 7. Azure Container Registry

**Used for:** storing Docker images built by GitLab CI.

> Terraform creates this automatically. Follow these steps only if creating manually.

### Create via Azure CLI

```bash
az acr create \
  --name videoextractdevacr \
  --resource-group video-extract-dev \
  --sku Basic \
  --admin-enabled true
```

### Create via Azure portal

1. Search **Container registries** → **Create**
2. **Basics:**
   - Resource group: `video-extract-dev`
   - Registry name: `videoextractdevacr` (must be globally unique)
   - Location: East US
   - SKU: Basic (dev), Standard (prod)
3. **Review + create** → **Create**
4. After creation: go to **Settings** → **Access keys** → enable **Admin user**

### Get the credentials

**Portal:**
Registry → **Settings** → **Access keys** → copy **Login server**, **Username**, **password**

**CLI:**
```bash
az acr credential show \
  --name videoextractdevacr \
  --query "{loginServer:loginServer, username:username, password:passwords[0].value}" \
  --output table
```

### Log in for local Docker builds

```bash
az acr login --name videoextractdevacr
```

### Where credentials are used

Terraform passes the ACR login server, username, and password directly to the `aca` module — they are wired in `infrastructure/terraform/envs/dev/main.tf` as `module.aca.acr_login_server`, `module.aca.acr_username`, and `module.aca.acr_password`. Each container app in `infrastructure/terraform/modules/aca/main.tf` uses them in its `registry {}` block as image pull credentials. You do not set them as application env vars.

For the GitLab CI pipeline (`build_images`, `push_to_acr` stages in `.gitlab-ci.yml`), add these as masked variables in GitLab → **Settings** → **CI/CD** → **Variables**:

| GitLab Variable | Value | Used in `.gitlab-ci.yml` |
|---|---|---|
| `ACR_REGISTRY` | Login server e.g. `videoextractdevacr.azurecr.io` | `docker login "$ACR_REGISTRY"` and all `docker build -t "$ACR_REGISTRY/..."` commands |
| `ACR_USERNAME` | ACR admin username | `docker login ... -u "$ACR_USERNAME"` |
| `ACR_PASSWORD` | ACR admin password | `echo "$ACR_PASSWORD" \| docker login ... --password-stdin` |

---

## 8. Azure Front Door

**Used for:** CDN delivery and signing time-limited download URLs for output videos.

> Terraform creates this automatically. Follow these steps only if retrieving the hostname manually.

### Get the endpoint hostname after Terraform apply

```bash
cd infrastructure/terraform/envs/dev
terraform output frontdoor_endpoint_hostname
```

**Portal:**
1. Search **Front Door and CDN profiles**
2. Open your profile → **Endpoints**
3. Copy the **Endpoint hostname** (e.g. `videoextract-dev.azurefd.net`)

**CLI:**
```bash
az afd endpoint show \
  --profile-name videoextract-dev-fd \
  --endpoint-name videoextract-dev \
  --resource-group video-extract-dev \
  --query hostName \
  --output tsv
```

### Get the signing secret

**Portal:**
1. Front Door profile → **Security** → **Secrets** → **+ Add**
2. Name: `url-signing-key`
3. Type: Customer managed
4. Certificate or secret value: generate a random string (min 16 chars), e.g.:
   ```bash
   openssl rand -base64 32
   ```
5. Copy and store the value — this is your `FRONT_DOOR_SECRET`

### Where to set it

| Context | File | Variables |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` — set `OUTPUT_URL_MODE=local` to bypass Front Door entirely; leave `FRONT_DOOR_ENDPOINT` and `FRONT_DOOR_SECRET` blank | `FRONT_DOOR_ENDPOINT`, `FRONT_DOOR_SECRET` |
| CI | GitLab → **Settings** → **CI/CD** → **Variables** (not needed — Terraform wires the endpoint hostname from `modules/frontdoor` output automatically) | — |
| Azure/ACA | Terraform injects automatically — endpoint hostname comes from `infrastructure/terraform/modules/frontdoor/outputs.tf` → `endpoint_hostname`, wired in `infrastructure/terraform/envs/dev/main.tf` → `module.aca.front_door_url`; injected as `FRONT_DOOR_URL` on `api-gateway` and as `FRONT_DOOR_HOSTNAME` on `notification-worker` in `infrastructure/terraform/modules/aca/main.tf` | `FRONT_DOOR_URL`, `FRONT_DOOR_HOSTNAME` |

> `FRONT_DOOR_SECRET` (the HMAC signing key) is generated manually and must be added to `infrastructure/terraform/envs/dev/terraform.tfvars` (gitignored) as `front_door_secret = "..."` or as a masked GitLab variable `TF_VAR_front_door_secret`.

---

## 9. Azure Communication Services

**Used for:** sending job completion emails via the notification-worker.

> Terraform creates the ACS resource automatically. You must manually verify a sender email domain.

### Get the connection string after Terraform apply

**Portal:**
1. Search **Communication Services**
2. Open `videoextract-dev-acs` → **Settings** → **Keys**
3. Copy **Primary Connection String**

**CLI:**
```bash
az communication list-key \
  --name videoextract-dev-acs \
  --resource-group video-extract-dev \
  --query primaryConnectionString \
  --output tsv
```

### Configure a verified sender email domain

Terraform creates the ACS resource, but you must set up a verified sender domain manually:

1. ACS resource → **Email** → **Domains** → **+ Add domain**
2. Choose one of:
   - **Azure-managed domain** — instant, uses `azurecomm.net` subdomain (good for dev/test)
   - **Custom domain** — requires DNS verification (required for prod)
3. For a custom domain:
   - Add the domain name
   - Azure will give you DNS TXT records to add to your DNS provider
   - Once DNS propagates, click **Verify**
4. Set the verified sender address in your env:
   ```
   SENDER_EMAIL=noreply@yourdomain.com
   ```

### Where to set it

| Context | File | Variable |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` — set `NOTIFICATION_MODE=stdout` to skip ACS entirely; leave `AZURE_COMMUNICATION_SERVICES_CONNECTION_STRING` blank | `AZURE_COMMUNICATION_SERVICES_CONNECTION_STRING` |
| CI | GitLab → **Settings** → **CI/CD** → **Variables** (not needed — Terraform wires the connection string from `modules/appcommunication` output automatically) | — |
| Azure/ACA | Terraform injects automatically — value comes from `infrastructure/terraform/modules/appcommunication/outputs.tf` → `primary_connection_string`, wired in `infrastructure/terraform/envs/dev/main.tf` → `module.aca.acs_connection_string`; injected as `AZURE_COMMUNICATION_SERVICES_CONNECTION_STRING` on the `notification-worker` container in `infrastructure/terraform/modules/aca/main.tf` | `AZURE_COMMUNICATION_SERVICES_CONNECTION_STRING` |

`SENDER_EMAIL` must be set manually in `infrastructure/docker-compose/.env` (local, though unused when `NOTIFICATION_MODE=stdout`) and added to `infrastructure/terraform/envs/dev/terraform.tfvars` or as a GitLab CI variable `TF_VAR_sender_email` so Terraform can inject it into the `notification-worker` container.

---

## 10. Application Insights

**Used for:** distributed tracing and telemetry across all services.

> Terraform creates this automatically. Follow these steps only if retrieving the connection string manually.

### Get the connection string after Terraform apply

**Portal:**
1. Search **Application Insights**
2. Open `videoextract-dev-ai` → **Overview**
3. Copy **Connection String** (starts with `InstrumentationKey=...;IngestionEndpoint=...`)

**CLI:**
```bash
az monitor app-insights component show \
  --app videoextract-dev-ai \
  --resource-group video-extract-dev \
  --query connectionString \
  --output tsv
```

### Where to set it

| Context | File | Variable |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` — leave `APPLICATIONINSIGHTS_CONNECTION_STRING=` empty; both the Node.js `applicationinsights` package and Python `azure-monitor-opentelemetry` package are no-ops when the value is absent | `APPLICATIONINSIGHTS_CONNECTION_STRING` |
| CI | GitLab → **Settings** → **CI/CD** → **Variables** (not needed — Terraform wires the connection string from `modules/appinsights` output automatically) | — |
| Azure/ACA | Terraform injects automatically — value comes from `infrastructure/terraform/modules/appinsights/main.tf` → `azurerm_application_insights.main.connection_string`, wired in `infrastructure/terraform/envs/dev/main.tf` → `module.aca.appinsights_connection_string`; injected as `APPLICATIONINSIGHTS_CONNECTION_STRING` on every container in `infrastructure/terraform/modules/aca/main.tf` | `APPLICATIONINSIGHTS_CONNECTION_STRING` |

---

## 11. Anthropic API Key

**Used by:** `agent-orchestrator` (agent reasoning), `mcp-server-analysis` (frontier vision tools)

### Get it

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Sign in → **API Keys** → **Create Key**
3. Name it (e.g. `video-extract-dev`)
4. Copy the key immediately — it is shown only once

### Where to set it

| Context | File | Variable |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` | `ANTHROPIC_API_KEY` |
| CI | GitLab → **Settings** → **CI/CD** → **Variables** — add masked variable `TF_VAR_anthropic_api_key`; read by the `&terraform-setup` anchor in `.gitlab-ci.yml` and passed to Terraform as `var.anthropic_api_key` declared in `infrastructure/terraform/envs/dev/variables.tf` | `TF_VAR_anthropic_api_key` |
| Azure/ACA | Terraform injects automatically — variable flows from `infrastructure/terraform/envs/dev/variables.tf` → `infrastructure/terraform/envs/dev/main.tf` → `module.aca.anthropic_api_key` → injected as `ANTHROPIC_API_KEY` on `agent-orchestrator` and `mcp-server-analysis` containers in `infrastructure/terraform/modules/aca/main.tf`; also stored in Key Vault as secret `anthropic-api-key` via `infrastructure/terraform/modules/keyvault/main.tf` | `ANTHROPIC_API_KEY` |

---

## 12. Optional: OpenAI API Key

Only needed if using `openai/gpt-4o` or similar for `AGENT_MODEL` / `TOOL_FRONTIER_MODEL`.

### Get it

1. Go to [platform.openai.com](https://platform.openai.com) → **API keys** → **Create new secret key**

### Where to set it

| Context | File | Variable |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` | `OPENAI_API_KEY` |
| CI | GitLab → **Settings** → **CI/CD** → **Variables** — add masked variable `TF_VAR_openai_api_key`; declared in `infrastructure/terraform/envs/dev/variables.tf` → `openai_api_key` | `TF_VAR_openai_api_key` |
| Azure/ACA | Terraform injects automatically — flows from `infrastructure/terraform/envs/dev/variables.tf` → `infrastructure/terraform/envs/dev/main.tf` → `module.aca.openai_api_key` → injected as `OPENAI_API_KEY` on `agent-orchestrator` and `mcp-server-analysis` containers in `infrastructure/terraform/modules/aca/main.tf` | `OPENAI_API_KEY` |

---

## 13. Optional: AWS Bedrock Credentials

Only needed if using `bedrock/` model strings for `AGENT_MODEL` / `TOOL_FRONTIER_MODEL`.

### Create an IAM user with Bedrock access

**AWS Console:**
1. Go to **IAM** → **Users** → **Create user**
2. Name: `video-extract-bedrock`
3. Permissions: **Attach policies directly** → search and attach `AmazonBedrockFullAccess`
4. Click **Create user**
5. Click on the user → **Security credentials** → **Create access key**
6. Use case: **Application running outside AWS**
7. Copy **Access key ID** and **Secret access key**

**AWS CLI:**
```bash
aws iam create-user --user-name video-extract-bedrock

aws iam attach-user-policy \
  --user-name video-extract-bedrock \
  --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess

aws iam create-access-key --user-name video-extract-bedrock
```

### Enable model access in Bedrock

In the AWS Console → **Amazon Bedrock** → **Model access** → request access to the models you want to use (e.g. Claude Sonnet, Nova). Access is per-region.

### Where to set it

| Context | File | Variables |
|---|---|---|
| Local dev | `infrastructure/docker-compose/.env` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME` |
| CI | GitLab → **Settings** → **CI/CD** → **Variables** — add masked variables `TF_VAR_aws_access_key_id` and `TF_VAR_aws_secret_access_key`; declared in `infrastructure/terraform/envs/dev/variables.tf` → `aws_access_key_id`, `aws_secret_access_key`, `aws_region_name` | `TF_VAR_aws_access_key_id`, `TF_VAR_aws_secret_access_key` |
| Azure/ACA | Terraform injects automatically — flows from `infrastructure/terraform/envs/dev/variables.tf` → `infrastructure/terraform/envs/dev/main.tf` → `module.aca.aws_*` → injected as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME` on `agent-orchestrator` and `mcp-server-analysis` containers in `infrastructure/terraform/modules/aca/main.tf` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME` |

---

## Full Local Dev Setup

You only need to do this once. Azurite and the Service Bus emulator handle Azure storage/messaging locally — no real Azure credentials needed for those.

```bash
# 1. Copy the example env file
cp infrastructure/docker-compose/.env.example infrastructure/docker-compose/.env

# 2. Open the file and set your LLM key (pick one):
#    ANTHROPIC_API_KEY=sk-ant-...
#    OPENAI_API_KEY=sk-...
#    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION_NAME

# 3. Everything else is already configured for local emulators:
#    - Azurite connection string pre-set
#    - Service Bus emulator connection string pre-set
#    - LOCAL_DEV_SKIP_AUTH=true  (skips Entra JWT validation)
#    - OUTPUT_URL_MODE=local     (skips Front Door signing)
#    - NOTIFICATION_MODE=stdout  (skips ACS email — prints to console)
#    - APPLICATIONINSIGHTS_CONNECTION_STRING= (empty = disabled)

# 4. Start the full stack
docker-compose -f infrastructure/docker-compose/docker-compose.yml up
```

---

## Full CI/CD Setup (GitLab)

All variables below are set in **GitLab → your project → Settings → CI/CD → Variables → Add variable**. Check **Masked** to hide the value in job logs.

| Variable | Description | Masked | Picked up by |
|---|---|---|---|
| `ARM_CLIENT_ID` | Terraform service principal app ID | yes | `&azure-login` and `&terraform-setup` anchors in `.gitlab-ci.yml` |
| `ARM_CLIENT_SECRET` | Terraform service principal password | yes | `&azure-login` and `&terraform-setup` anchors in `.gitlab-ci.yml` |
| `ARM_SUBSCRIPTION_ID` | Azure subscription ID | yes | `&azure-login` and `&terraform-setup` anchors in `.gitlab-ci.yml` |
| `ARM_TENANT_ID` | Azure AD tenant ID | yes | `&azure-login` and `&terraform-setup` anchors in `.gitlab-ci.yml` |
| `ACR_REGISTRY` | ACR login server (e.g. `videoextractdevacr.azurecr.io`) | no | `&docker-login` anchor; all `build_images` and `push_to_acr` jobs in `.gitlab-ci.yml` |
| `ACR_USERNAME` | ACR admin username | yes | `&docker-login` anchor in `.gitlab-ci.yml` |
| `ACR_PASSWORD` | ACR admin password | yes | `&docker-login` anchor in `.gitlab-ci.yml`; also passed to Terraform as `TF_VAR_acr_password` in `&terraform-setup` |
| `DB_ADMIN_PASSWORD` | PostgreSQL container admin password | yes | `&terraform-setup` anchor → `TF_VAR_db_admin_password` → `infrastructure/terraform/envs/dev/variables.tf` → `module.aca.db_admin_password` |
| `TF_STATE_STORAGE_ACCOUNT` | Terraform state storage account name (`tfstatevideoextract`) | no | `&terraform-setup` anchor → `terraform init -backend-config` in `.gitlab-ci.yml` |
| `TF_VAR_anthropic_api_key` | Anthropic API key | yes | `&terraform-setup` anchor → `infrastructure/terraform/envs/dev/variables.tf` → `module.aca` → `ANTHROPIC_API_KEY` on containers |
| `TF_VAR_entra_tenant_id` | Entra External ID tenant ID | no | `&terraform-setup` anchor → `infrastructure/terraform/envs/dev/variables.tf` → `module.aca` → `ENTRA_TENANT_ID` on `api-gateway` |
| `TF_VAR_entra_client_id` | Entra External ID app/client ID | no | `&terraform-setup` anchor → `infrastructure/terraform/envs/dev/variables.tf` → `module.aca` → `ENTRA_CLIENT_ID` on `api-gateway` |
| `TF_VAR_openai_api_key` | OpenAI key (only if using OpenAI models) | yes | `&terraform-setup` anchor → `infrastructure/terraform/envs/dev/variables.tf` → `module.aca` → `OPENAI_API_KEY` on containers |
| `TF_VAR_aws_access_key_id` | AWS access key (only if using Bedrock) | yes | `&terraform-setup` anchor → `infrastructure/terraform/envs/dev/variables.tf` → `module.aca` → `AWS_ACCESS_KEY_ID` on containers |
| `TF_VAR_aws_secret_access_key` | AWS secret key (only if using Bedrock) | yes | `&terraform-setup` anchor → `infrastructure/terraform/envs/dev/variables.tf` → `module.aca` → `AWS_SECRET_ACCESS_KEY` on containers |

All other connection strings (Blob Storage, Service Bus, ACS, Application Insights, database URL) are wired automatically by Terraform in `infrastructure/terraform/envs/dev/main.tf` — you do not set them as GitLab variables. The database URL in particular is constructed entirely inside `modules/aca/main.tf` from the `db_admin_password` variable — no separate database module or output is involved.

---

## Security Rules

- **Never commit `.env` files** — they are gitignored
- **Never commit `terraform.tfvars`** — gitignored; use `TF_VAR_` env vars in CI instead
- **Use `.env.example` files** to document required variables with placeholder values
- **Production secrets** are stored in Azure Key Vault by Terraform and will be wired to ACA managed identities in Phase B
- **Rotate a key** by updating the value in Key Vault — ACA picks it up on the next revision
- **ACR admin credentials** are sensitive — treat them like passwords; they are stored in Terraform state (which is encrypted at rest in the state storage account)
