FROM node:22-alpine AS build

WORKDIR /app

COPY apps/console/package.json ./package.json
RUN npm install --no-audit --no-fund

COPY apps/console/ ./

ARG VITE_API_BASE_URL=https://api.ai-supernova.com
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

FROM nginx:1.28-alpine

COPY apps/console/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
