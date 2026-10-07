# SvelteKit adapter-static build served as static files (PRD-001 §5, §10), with nginx
# also reverse-proxying /admin/v1 to console-api so the SPA and API share one origin —
# same pattern as operations-advws's deploy/nginx/configmap.yaml.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG PUBLIC_CONSOLE_API_BASE_URL=/admin/v1
ENV PUBLIC_CONSOLE_API_BASE_URL=$PUBLIC_CONSOLE_API_BASE_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
