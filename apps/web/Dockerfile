FROM node:24-alpine AS builder
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.pnpm-store \
    corepack prepare pnpm@11.13.1 --activate && \
    pnpm config set fetch-retries 5 && \
    pnpm install --frozen-lockfile --store-dir /root/.pnpm-store
COPY index.html vite.config.ts tsconfig.json uno.config.ts ./
COPY src ./src
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
