#!/bin/sh
set -e

# Replace env var placeholders baked into the Angular build with runtime values.
# Scoped substitution avoids accidentally replacing unrelated ${...} patterns.
find /usr/share/nginx/html -name "*.js" | while read -r file; do
  envsubst '${AZURE_ENTRA_CLIENT_ID}${AZURE_ENTRA_TENANT_ID}${APP_BASE_URL}${LOCAL_DEV_SKIP_AUTH}' \
    < "$file" > "$file.tmp" && mv "$file.tmp" "$file"
done

exec nginx -g 'daemon off;'
