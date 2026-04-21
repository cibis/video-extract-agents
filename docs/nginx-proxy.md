# nginx Reverse Proxy — angular-shell

## Role in the project

The `angular-shell` container is the single public-facing entry point in both local (Docker Compose) and cloud (Azure Container Apps) environments. It does two things:

1. **Serves the Angular SPA** — static files built into `/usr/share/nginx/html`
2. **Routes all other traffic** — acts as a reverse proxy to internal services

Without nginx, there would be no way to serve the Angular app and proxy API + chat traffic from a single origin. Splitting into multiple origins would require CORS configuration on every service and would prevent same-origin iframe embedding for LibreChat.

---

## Why nginx is necessary

### Single origin for browser requests

The Angular app makes relative API calls (`/api/v1/jobs`, `/api/v1/videos`) rather than absolute URLs in production. Those requests land on the angular-shell container. nginx routes them to the correct internal service.

### LibreChat iframe same-origin embedding

LibreChat is embedded in an Angular `<iframe src="/chat">`. Because both Angular and LibreChat are served from the same origin through nginx, the browser treats the iframe as same-origin. This enables:
- `window.postMessage` communication between Angular shell and LibreChat without cross-origin restrictions
- Session cookies shared across both UIs if needed

Using separate hostnames would require explicit CORS headers, relaxed CSP, and `allow-same-origin` on the iframe `sandbox` attribute — all fragile to maintain.

### Single ACA ingress

Azure Container Apps exposes one external HTTPS endpoint per container. All internal services (api-gateway, librechat, agent-orchestrator) are on the private internal network only. nginx in angular-shell is the gateway that routes external traffic to those internal services.

---

## Route table

| Request path | nginx action | Backend |
|---|---|---|
| `/health` | Inline `return 200` | — (nginx itself) |
| `/api/v1/*` | Strip `/api`, proxy to `API_GATEWAY_URL` | api-gateway `:8000` |
| `/api/*` | Proxy as-is | librechat `:3080` |
| `/assets/*` | Proxy as-is | librechat `:3080` |
| `/registerSW.js`, `/sw.js` | Proxy as-is | librechat `:3080` |
| `/chat/*` | Strip `/chat`, proxy to `LIBRECHAT_URL` | librechat `:3080` |
| `/*` (unmatched) | `try_files → /index.html` | Angular SPA |

**Location matching note:** nginx uses longest-prefix matching for `location` blocks, so `/api/v1/` wins over `/api/` for all api-gateway calls, even though `/api/` also matches.

---

## Configuration approach

`nginx.conf` is a **template** — it contains `${API_GATEWAY_URL}` and `${LIBRECHAT_URL}` placeholders rather than hardcoded hostnames. At container startup, `docker-entrypoint.sh` runs `envsubst` to produce the final config:

```sh
envsubst '${API_GATEWAY_URL}${LIBRECHAT_URL}' \
  < /etc/nginx/conf.d/default.conf.template \
  > /etc/nginx/conf.d/default.conf
```

The substitution is **scoped** — only those two variables are replaced. nginx's own variables (`$host`, `$remote_addr`, `$scheme`, etc.) are left untouched.

Environment values per deployment:

| Variable | Local (Docker Compose) | ACA (dev / prod) |
|---|---|---|
| `API_GATEWAY_URL` | `http://api-gateway:8000` | `http://api-gateway` |
| `LIBRECHAT_URL` | `http://librechat:3080` | `http://librechat` |

ACA internal services are exposed on port 80 regardless of what port the container listens on, so no port suffix is needed there.

---

## Host header — ACA internal routing

All proxy locations use `proxy_set_header Host $proxy_host;` rather than `$host`.

`$host` is the client's original Host value — e.g. `angular-shell.victoriousbeach-653526f7.eastus.azurecontainerapps.io`. ACA's internal load balancer uses virtual-host routing: it matches the Host header against the target container app's registered name. Forwarding the angular-shell FQDN to api-gateway or librechat produces a Host-not-found match and ACA returns its "Unavailable" 404 page.

`$proxy_host` is the upstream hostname from the `proxy_pass` directive — `api-gateway` or `librechat`. ACA's internal load balancer recognises this as the correct target and routes the request to the container.

---

## DNS resolution strategy

`proxy_pass` values are **literals after envsubst** (not the `set $var; proxy_pass $var` pattern). This means nginx resolves upstreams using the OS resolver at startup, which honours `/etc/resolv.conf` search domains.

In ACA, `/etc/resolv.conf` contains:
```
search k8se-apps.svc.cluster.local ...
options ndots:5
nameserver 100.100.224.10
```

The short hostname `api-gateway` is resolved to the Kubernetes ClusterIP by appending the search domain. The `set $var` pattern bypasses this and sends raw DNS queries directly to the nameserver — which does not know short names — causing `Host not found` 502 errors.

In Docker Compose, Docker's embedded DNS (`127.0.0.11`) resolves container names directly, so both patterns work locally. The literal `proxy_pass` approach works in both environments.

---

## SSE (Server-Sent Events)

The `/api/v1/` location includes `proxy_buffering off`. This is required for the job progress stream endpoint (`GET /v1/jobs/:id/stream`) — nginx's default response buffering would hold SSE events in memory and deliver them in bursts rather than forwarding them immediately to the browser.

---

## Caching

- **Entry point files** (`index.html`, `main.js`, `polyfills.js`, `styles.css`) — `no-cache`. These have no content hash and must always be fetched fresh so users get the latest Angular build.
- **Content-hashed chunks** (`*.js`, `*.css`, fonts, images) — `1y` immutable. These filenames change with every build, so cached copies are always valid.
