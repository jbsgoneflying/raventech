FROM node:20-alpine AS base
WORKDIR /app

# Install dependencies
COPY packages/shared/package.json packages/shared/
COPY apps/web/package.json apps/web/
COPY package.json turbo.json tsconfig.base.json ./

RUN npm install --workspace=packages/shared --workspace=apps/web

# Build shared
COPY packages/shared packages/shared
RUN npm run build --workspace=packages/shared

# Build web
COPY apps/web apps/web
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build --workspace=apps/web

# Production image
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=base /app/node_modules ./node_modules
COPY --from=base /app/packages/shared/dist ./packages/shared/dist
COPY --from=base /app/packages/shared/package.json ./packages/shared/
COPY --from=base /app/apps/web/.next ./apps/web/.next
COPY --from=base /app/apps/web/public ./apps/web/public
COPY --from=base /app/apps/web/package.json ./apps/web/
COPY --from=base /app/apps/web/next.config.js ./apps/web/

EXPOSE 3000

WORKDIR /app/apps/web
CMD ["npx", "next", "start", "--port", "3000"]
