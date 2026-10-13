# syntax=docker/dockerfile:1

# ---- build the static bundle ----
FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
# --ignore-scripts: no third-party install hooks run during the image build.
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts
COPY . .
RUN npm run build

# ---- serve with nginx as a non-root user (uid 101), port 8080 ----
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY api_proxy.conf /etc/nginx/api_proxy.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 8080
