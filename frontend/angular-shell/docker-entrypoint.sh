#!/bin/sh
set -e

# Detect the DNS nameserver from /etc/resolv.conf so nginx can re-resolve
# upstream hostnames dynamically. Works in Docker Compose (127.0.0.11) and ACA.
NGINX_RESOLVER=$(grep nameserver /etc/resolv.conf | awk 'NR==1{print $2}')
export NGINX_RESOLVER

# Generate final nginx config from template, substituting upstream URLs and resolver.
# Scoped substitution leaves nginx's own $host, $remote_addr etc. untouched.
envsubst '${NGINX_RESOLVER}${API_GATEWAY_URL}${LIBRECHAT_URL}' \
  < /etc/nginx/conf.d/default.conf.template \
  > /etc/nginx/conf.d/default.conf

# Replace env var placeholders baked into the Angular build with runtime values.
find /usr/share/nginx/html -name "*.js" | while read -r file; do
  envsubst '${AZURE_ENTRA_CLIENT_ID}${AZURE_ENTRA_TENANT_ID}${APP_BASE_URL}${LOCAL_DEV_SKIP_AUTH}' \
    < "$file" > "$file.tmp" && mv "$file.tmp" "$file"
done

exec nginx -g 'daemon off;'
