# LibreChat fork Dockerfile
# Clones upstream LibreChat, applies project-specific customisations, builds and serves.
FROM node:20-alpine AS base
WORKDIR /app

# Clone upstream LibreChat (pinned to a known-good release tag)
# In CI, replace with COPY of the fork if the full fork is checked in
RUN apk add --no-cache git && \
    git clone --depth 1 --branch v0.7.6 https://github.com/danny-avila/LibreChat.git . && \
    rm -rf .git

# Apply project-specific config
COPY librechat.yaml ./librechat.yaml
COPY .env.example ./.env.example

# Apply platform customisations (React components, branding)
COPY client/src/platform/ ./client/src/platform/

# Copy the SSO bootstrap script into the Vite public directory so it is
# emitted verbatim to the build output root and served at /platform-init.js.
# It must run before React hydrates to pre-populate localStorage with the
# LibreChat session token passed by the Angular shell in the iframe URL.
COPY client/public/platform-init.js ./client/public/platform-init.js

# Copy platform CSS overrides into the Vite public directory so they are
# emitted verbatim to dist/ and served at /BrandingOverride.css.
# LibreChat uses Tailwind (no sass), so overrides are injected as a plain
# stylesheet rather than imported into the SCSS build.
COPY client/public/BrandingOverride.css ./client/public/BrandingOverride.css

# Copy platform logo and favicon SVGs — served at /logo.svg and /favicon.svg.
COPY client/public/logo.svg ./client/public/logo.svg
COPY client/public/favicon.svg ./client/public/favicon.svg

# Inject the bootstrap script and platform stylesheet into index.html
# immediately before </head>. The <script> runs synchronously before React
# hydrates; the <link> loads last so it overrides all bundle styles.
RUN BUILD_TS=$(date +%s) && \
    sed -i "s|</head>|  <script src=\"/platform-init.js\"></script>\n  <link rel=\"stylesheet\" href=\"/BrandingOverride.css?v=${BUILD_TS}\">\n  </head>|" client/index.html

# Patch the agents endpoint to be gated on AGENTS_ENABLED env var.
# By default (env var absent/empty) the agents endpoint is disabled so
# LibreChat cannot route prompts to /agents/:undefined and fall back to
# an "Agent not found" error.  Set AGENTS_ENABLED=true to re-enable.
RUN node -e "\
  const fs = require('fs');\
  let src = fs.readFileSync('api/server/services/Config/EndpointService.js', 'utf8');\
  src = src.replace(\
    \"generateConfig('true', undefined, EModelEndpoint.agents)\",\
    \"generateConfig(process.env.AGENTS_ENABLED || '', undefined, EModelEndpoint.agents)\"\
  );\
  fs.writeFileSync('api/server/services/Config/EndpointService.js', src);\
  console.log('Agents endpoint patch applied');\
"

# Patch the welcome message shown on the new-chat landing screen.
# Walk client/src/ with Node.js fs to find the i18n file containing the key.
RUN node -e "\
  const fs = require('fs');\
  const path = require('path');\
  function walk(dir) {\
    let results = [];\
    fs.readdirSync(dir).forEach(f => {\
      const full = path.join(dir, f);\
      if (fs.statSync(full).isDirectory()) results = results.concat(walk(full));\
      else if (/\.(ts|tsx|json)$/.test(f)) results.push(full);\
    });\
    return results;\
  }\
  const files = walk('client/src');\
  let patched = 0;\
  files.forEach(file => {\
    let src = fs.readFileSync(file, 'utf8');\
    if (src.includes('com_nav_welcome_message') && src.includes('How can I help you today?')) {\
      src = src.replace('How can I help you today?', 'Describe what to extract');\
      fs.writeFileSync(file, src);\
      console.log('Welcome message patch applied to', file);\
      patched++;\
    }\
  });\
  if (!patched) console.log('WARNING: com_nav_welcome_message not found — welcome text unchanged');\
"

# Patch CORS to support credentials from the Angular shell origin.
# LibreChat's default cors() returns Access-Control-Allow-Origin: * which is
# incompatible with credentials: 'include'.  When ANGULAR_ORIGIN is set, the
# server returns the specific origin + Access-Control-Allow-Credentials: true,
# allowing the browser to store the refreshToken HttpOnly cookie that is set
# on login — the cookie that drives LibreChat's session on subsequent loads.
RUN node -e "\
  const fs = require('fs');\
  let src = fs.readFileSync('api/server/index.js', 'utf8');\
  src = src.replace(\
    'app.use(cors());',\
    'app.use(cors(process.env.ANGULAR_ORIGIN ? { origin: process.env.ANGULAR_ORIGIN, credentials: true } : {}));'\
  );\
  fs.writeFileSync('api/server/index.js', src);\
  console.log('CORS patch applied');\
"

# Patch server-level timeouts so long-running agent calls never cause a
# "socket hang up" on the LibreChat side.  Set socket timeout to 0 (disabled)
# and keep-alive to 1 hour; headersTimeout must exceed keepAliveTimeout.
RUN node -e "\
  const fs = require('fs');\
  let src = fs.readFileSync('api/server/index.js', 'utf8');\
  src = src.replace(\
    /server\.listen\(/,\
    'server.setTimeout(0);\nserver.keepAliveTimeout = 3600000;\nserver.headersTimeout = 3601000;\nserver.listen('\
  );\
  fs.writeFileSync('api/server/index.js', src);\
  console.log('Server timeout patch applied');\
"

# Patch main.tsx to mount platform bridge components (AuthBridge + JobStatusBridge)
# alongside the LibreChat app so they receive postMessage events from the Angular shell.
RUN node -e "\
  const fs = require('fs');\
  const p = 'client/src/main.jsx';\
  let src = fs.readFileSync(p, 'utf8');\
  if (!src.includes('JobStatusBridge')) {\
    const imp = \"import { createElement as _pCE, Fragment as _pFrag } from 'react';\\nimport { createRoot as _pCR } from 'react-dom/client';\\nimport { JobStatusBridge as _PlatformJobBridge } from './platform/JobStatusBridge';\\nimport { AuthBridge as _PlatformAuthBridge } from './platform/AuthBridge';\\n\";\
    const mnt = \"\\nconst _lcPlatformBridge = document.createElement('div');\\ndocument.body.appendChild(_lcPlatformBridge);\\n_pCR(_lcPlatformBridge).render(_pCE(_pFrag, null, _pCE(_PlatformAuthBridge, null), _pCE(_PlatformJobBridge, null)));\\n\";\
    fs.writeFileSync(p, imp + src + mnt);\
    console.log('Platform bridge mount patch applied to main.tsx');\
  } else {\
    console.log('Platform bridge already present — skip');\
  }\
"

# Install dependencies and build
FROM base AS builder
RUN npm install -g bun
RUN npm ci --legacy-peer-deps
RUN npm run --workspace=packages/data-provider build
RUN npm run b:client

# Production image
FROM node:20-alpine AS production
WORKDIR /app
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

# gettext provides envsubst, used by entrypoint.sh to resolve ${VAR} references
# in librechat.yaml before the server starts.
RUN apk add --no-cache gettext

# Copy built assets + server
COPY --from=builder /app/client/dist ./client/dist
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/packages ./packages
COPY --from=builder /app/api ./api
COPY --from=builder /app/librechat.yaml ./librechat.yaml
COPY --from=builder /app/package.json ./package.json

# entrypoint.sh runs envsubst on librechat.yaml then execs the server.
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# librechat.yaml must be writable by appuser so entrypoint.sh can overwrite it
# with the envsubst-resolved version.
RUN mkdir -p /app/api/logs /app/data && \
    chown -R appuser:appgroup /app/api/logs /app/data && \
    chown appuser:appgroup /app/librechat.yaml /app/entrypoint.sh

USER appuser
EXPOSE 3080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3080/api/health || exit 1
CMD ["/app/entrypoint.sh"]
