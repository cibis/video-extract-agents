# Seeing Compilation Errors Instantly During Local Development

This guide explains how to get immediate feedback on type and syntax errors in each service of the stack without waiting for a full build or Docker rebuild.

---

## Table of Contents

- [1. Node.js API Gateway (TypeScript)](#1-nodejs-api-gateway-typescript)
  - [Watch mode with ts-node-dev](#watch-mode-with-ts-node-dev)
  - [Type-check only (no emit)](#type-check-only-no-emit)
  - [VS Code — Problems panel](#vs-code-problems-panel)
- [2. Angular Shell (TypeScript)](#2-angular-shell-typescript)
  - [ng serve — live compilation](#ng-serve-live-compilation)
  - [Type-check only](#type-check-only)
  - [VS Code](#vs-code)
- [3. Python Services (FastAPI + Uvicorn)](#3-python-services-fastapi-uvicorn)
  - [a) Syntax and import errors — caught at startup](#a-syntax-and-import-errors-caught-at-startup)
  - [b) Type errors — caught by Pyright / mypy](#b-type-errors-caught-by-pyright-mypy)
- [4. Recommended Terminal Layout](#4-recommended-terminal-layout)
- [5. Angular Shell — LibreChat iframe not showing](#5-angular-shell-librechat-iframe-not-showing)
  - [a) LibreChat is not running](#a-librechat-is-not-running)
  - [b) Angular sanitizes the iframe `[src]` binding](#b-angular-sanitizes-the-iframe-src-binding)
- [6. ESLint (TypeScript — optional real-time linting)](#6-eslint-typescript-optional-real-time-linting)
- [6. Summary](#6-summary)

---

## 1. Node.js API Gateway (TypeScript)

**Location:** `backend/api-gateway/`

### Watch mode with ts-node-dev

The `dev` script uses `ts-node-dev`, which transpiles and restarts on every file save. Errors are printed to the terminal immediately.

```bash
cd backend/api-gateway
npm install
npm run dev
```

Errors appear inline:

```
ERROR in src/routes/jobs.ts:42:18
TS2345: Argument of type 'string | undefined' is not assignable to parameter of type 'string'.
```

### Type-check only (no emit)

To run the TypeScript compiler in check mode without producing output files — useful in a second terminal to catch type errors independently of the running server:

```bash
cd backend/api-gateway
npx tsc --noEmit --watch
```

This watches all files in `src/` and reports every type error as you edit, with zero output files written.

### VS Code — Problems panel

The TypeScript Language Server runs automatically inside VS Code. Errors appear:

- As red underlines in the editor
- In **View → Problems** (`Ctrl+Shift+M` / `Cmd+Shift+M`)
- In the **Editor tab** title (red dot)

No extra setup is needed. The `tsconfig.json` at `backend/api-gateway/tsconfig.json` is picked up automatically.

If VS Code shows errors that the compiler does not (or vice versa), check that VS Code is using the workspace TypeScript version, not its bundled one:

1. Open any `.ts` file
2. Click the TypeScript version in the bottom-right status bar
3. Select **Use Workspace Version**

---

## 2. Angular Shell (TypeScript)

**Location:** `frontend/angular-shell/`

### ng serve — live compilation

```bash
cd frontend/angular-shell
npm install
npm run start          # calls ng serve
```

`ng serve` compiles incrementally on every save. Errors are printed to the terminal and also surfaced in the browser as an overlay:

```
ERROR in src/app/upload/upload.component.ts:15:5
TS2322: Type 'number' is not assignable to type 'string'.
```

The browser overlay blocks rendering until the error is fixed, giving you instant visual feedback.

### Type-check only

```bash
cd frontend/angular-shell
npx tsc --noEmit --watch -p tsconfig.app.json
```

This uses `tsconfig.app.json` (application sources only, excludes test files).

### VS Code

The Problems panel works the same as for the API Gateway. Angular's `tsconfig.app.json` and `tsconfig.json` are both picked up by the language server.

---

## 3. Python Services (FastAPI + Uvicorn)

Python is interpreted, so there is no compilation step. Errors fall into two categories:

### a) Syntax and import errors — caught at startup

Uvicorn discovers these when it first imports the module. They appear immediately in the terminal when starting a service in reload mode:

```bash
cd backend/agent-orchestrator
poetry install
poetry run uvicorn app.main:app --reload --port 8001
```

```
ERROR:    Error loading ASGI app. Could not import module "app.main".
  File "app/main.py", line 12
    def foo(x: int
                  ^
SyntaxError: '(' was never closed
```

The `--reload` flag also restarts on every file save, so syntax errors in new edits surface within a second.

### b) Type errors — caught by Pyright / mypy

Python type annotations are not checked at runtime. Use a static type checker to catch them instantly:

**Option A — Pyright via VS Code (recommended)**

Install the [Pylance extension](https://marketplace.visualstudio.com/items?itemName=ms-python.vscode-pylance) (bundled with the Python extension). It runs Pyright continuously and shows type errors in the Problems panel and as inline squiggles. No configuration is required for basic checking.

To increase strictness, add a `pyrightconfig.json` at the service root:

```json
{
  "typeCheckingMode": "standard",
  "venvPath": ".",
  "venv": ".venv"
}
```

**Option B — mypy in a terminal**

```bash
cd backend/agent-orchestrator
poetry run mypy app/ --ignore-missing-imports
```

For continuous watch mode install `mypy-daemon`:

```bash
poetry add --group dev mypy
poetry run dmypy run -- app/
```

`dmypy` caches results and re-checks only changed files, giving near-instant feedback on large services.

Repeat the same setup for:
- `backend/preprocessing-worker/`
- `backend/notification-worker/`
- `mcp-servers/mcp-server-analysis/`
- `mcp-servers/mcp-server-processing/`

---

## 4. Recommended Terminal Layout

Run one watcher per service type. A four-pane terminal layout covers the full stack:

| Pane | Command | Service |
|---|---|---|
| 1 | `npm run dev` | api-gateway (runs + reports TS errors) |
| 2 | `npx tsc --noEmit --watch` | api-gateway (type-check only, clean output) |
| 3 | `npm run start` (ng serve) | angular-shell |
| 4 | `uvicorn app.main:app --reload` | agent-orchestrator (or any Python service) |

VS Code's split terminal (`Ctrl+Shift+5`) or terminal tabs keep all four visible simultaneously.

---

## 5. Angular Shell — LibreChat iframe not showing

When running only `npm run start` in `frontend/angular-shell/`, the LibreChat iframe will appear blank or show a connection error. There are two reasons:

### a) LibreChat is not running

`npm run start` starts only the Angular shell on `:4200`. The iframe points to `http://localhost:3080` (`src/environments/environment.ts`), but LibreChat is a separate service that only starts via `docker-compose up`. With nothing listening on `:3080`, the iframe loads a blank or error page.

To run the full local stack:

```bash
cd infrastructure/docker-compose
docker-compose up
```

### b) Angular sanitizes the iframe `[src]` binding

In `src/app/features/shared/librechat-iframe/librechat-iframe.component.ts`, `librechatUrl` is a plain `string` bound directly to `[src]`. Angular's `DomSanitizer` will strip iframe URLs that are not explicitly marked as safe. The browser console will show:

```
WARNING: sanitizing unsafe URL value http://localhost:3080
```

The fix is to mark the URL as a `SafeResourceUrl`:

```ts
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { inject } from '@angular/core';

// inside the class — replace the plain string assignment:
private sanitizer = inject(DomSanitizer);
librechatUrl: SafeResourceUrl = this.sanitizer.bypassSecurityTrustResourceUrl(environment.librechatUrl);
```

---

## 6. ESLint (TypeScript — optional real-time linting)

The API Gateway has ESLint configured. Run it in watch mode alongside the compiler:

```bash
cd backend/api-gateway
npx eslint --watch src/ --ext .ts
```

Or install the [ESLint VS Code extension](https://marketplace.visualstudio.com/items?itemName=dbaeumer.vscode-eslint) to see lint errors inline without a separate terminal.

---

## 6. Summary

| Service | Instant error feedback |
|---|---|
| `backend/api-gateway/` | `npm run dev` (runtime) + `tsc --noEmit --watch` (types) |
| `frontend/angular-shell/` | `npm run start` (`ng serve`) — terminal + browser overlay |
| Python services | `uvicorn --reload` (syntax/import) + Pylance/Pyright in VS Code (types) |
| All TypeScript | VS Code Problems panel (`Ctrl+Shift+M`) — always on |
| All Python | Pylance extension squiggles + Problems panel — always on |
