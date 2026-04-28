# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS build

WORKDIR /app

# Install pnpm via corepack
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

# Manifest first for cache reuse.
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
