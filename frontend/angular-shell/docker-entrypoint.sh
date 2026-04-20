#!/bin/sh
set -e

# Generate final nginx config from template, substituting upstream URLs.
# Scoped substitution leaves nginx's own $host, $remote_addr etc. untouched.
# Values differ per environment:
#   Docker Compose: API_GATEWAY_URL=http://api-gateway:8000, LIBRECHAT_URL=http://librechat:3080
#   ACA:            API_GATEWAY_URL=http://api-gateway,      LIBRECHAT_URL=http://librechat
envsubst '${API_GATEWAY_URL}${LIBRECHAT_URL}' \
  < /etc/nginx/conf.d/default.conf.template \
  > /etc/nginx/conf.d/default.conf

# Replace env var placeholders baked into the Angular build with runtime values.
find /usr/share/nginx/html -name "*.js" | while read -r file; do
  envsubst '${AZURE_ENTRA_CLIENT_ID}${AZURE_ENTRA_TENANT_ID}${APP_BASE_URL}${LOCAL_DEV_SKIP_AUTH}' \
    < "$file" > "$file.tmp" && mv "$file.tmp" "$file"
done

exec nginx -g 'daemon off;'
