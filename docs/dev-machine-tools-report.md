# Dev Machine Tools Report

**Date:** 2026-03-09
**Platform:** Windows 11 Pro — Docker Desktop with WSL 2

---

## Prerequisites Status (SETUP.md §1)

| Tool | Required | Installed | Status |
|---|---|---|---|
| Docker Desktop | ≥ 4.30 | Engine 26.1.1, Desktop running, Compose v2.27.0 | OK |
| WSL 2 (Ubuntu) | latest | Only `docker-desktop` distros — no Ubuntu | Not required — Linux containers work via the `docker-desktop` distro; Git Bash covers all shell scripts |
| Git | ≥ 2.45 | 2.45.2.windows.1 | OK |
| Node.js | 22 LTS | v20.14.0 | OK with caveat — SETUP.md says 22, but `api-gateway/Dockerfile` is `node:20-alpine`; local and container are consistent at v20. Both will hit EOL in April 2026. |
| Python | 3.11 | 3.12.4 | OK — all `pyproject.toml` files declare `python = "^3.11"` which allows 3.12; `poetry install` will succeed. Containers use `python:3.11-slim` and are unaffected. |
| Poetry | 1.8.x | 1.8.3 | OK — installed via official installer; binary at `C:\Users\i8329\AppData\Roaming\Python\Scripts\poetry.exe`; PATH updated |
| Angular CLI | 19 | 19.2.22 | OK — installed globally via npm |
| Terraform | ≥ 1.6 | v1.13.5 | OK |
| Azure CLI | ≥ 2.63 | 2.79.0 | OK — minor update available via `az upgrade` |
| GitLab CLI (`glab`) | latest | 1.89.0 | OK — installed via winget (GLab.GLab); binary at `C:\Users\i8329\AppData\Local\Programs\glab\glab.exe` |

---

## All tools installed — nothing missing

---

## Action worth noting (not a blocker today)

Node.js 20 reaches end-of-life **April 2026** — roughly four weeks away from the date of this report.
This affects both the local install and `api-gateway/Dockerfile`.
No action is urgent today, but upgrading both from `20` → `22` is the next planned step when ready.
