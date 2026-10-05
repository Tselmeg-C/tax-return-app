# web image for Railway: TanStack Start built as a Nitro node server (.output/).
# No secrets in ARG/ENV: API_INTERNAL_URL, OTEL_* come from Railway variables at runtime.

FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-slim AS runtime
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000
WORKDIR /app
# Nitro bundles the server and its production dependencies into .output/.
COPY --from=build --chown=node:node /app/.output ./.output
COPY --from=build --chown=node:node /app/package.json ./package.json
USER node
EXPOSE 3000
# Same as `npm run start`, but node is PID 1 so SIGTERM reaches the server directly.
CMD ["node", ".output/server/index.mjs"]
