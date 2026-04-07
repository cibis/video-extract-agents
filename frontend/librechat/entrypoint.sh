#!/bin/sh
set -e

# Substitute ${VAR} references in librechat.yaml at container startup.
#
# LibreChat's own extractEnvVariable() only resolves whole-string references
# (e.g. apiKey: "${MY_KEY}").  Embedded references such as
#   baseURL: "${API_GATEWAY_URL}/v1/chat"
# are left unresolved, producing an invalid URL at runtime.
#
# envsubst processes all ${VAR} occurrences in the file regardless of position,
# so every environment variable available to the container is substituted.

envsubst < /app/librechat.yaml > /tmp/librechat.yaml.resolved
cat /tmp/librechat.yaml.resolved > /app/librechat.yaml
rm /tmp/librechat.yaml.resolved

exec node api/server/index.js
