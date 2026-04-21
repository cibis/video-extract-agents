# Build stage
FROM node:20-alpine AS builder
WORKDIR /app
ARG BUILD_CONFIG=production
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build -- --configuration=$BUILD_CONFIG

# Production stage — nginx static file server
FROM nginx:alpine AS production
COPY --from=builder /app/dist/angular-shell/browser /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf.template
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost/health || exit 1
ENTRYPOINT ["/docker-entrypoint.sh"]
